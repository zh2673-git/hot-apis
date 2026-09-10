"""工具调用适配层 · 验证脚本

两部分：
  1) 单测（离线，无需 Token）：断言 A-R1/R2/R3、A-P1~P5、extract 多格式容错（F1–F5）
  2) L 维度（真实请求）：用例集 U1–U5，统计各平台工具调用成功率（门槛 >= 80%）

用法：
    python verify_tools.py                 # 只跑单测
    python verify_tools.py --llm           # 单测 + L 维度（默认 deepseek/kimi/doubao）
    python verify_tools.py --llm --platforms deepseek kimi doubao zhipu
    python verify_tools.py --unit-only
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from src.models import ChatCompletionRequest, ChatMessage, ToolDef
from src.tools import (
    ToolCallStreamParser,
    extract,
    prepare,
    render_messages,
    to_tool_calls,
)
from src.tools.spec import EventKind, ParseState, TOOL_CALL_OPEN

# ---------------------------------------------------------------- 用例工具定义

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}

TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "查询指定城市的当前时间",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}


def tool_defs(*specs) -> list:
    return [ToolDef.model_validate(spec) for spec in specs]


def make_request(model: str, messages: list, tools: list, stream: bool = False,
                 tool_choice=None) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model, messages=messages, tools=tools,
        tool_choice=tool_choice, stream=stream,
    )


# ================================================================ 单测


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  [PASS] {name}")
        else:
            self.failed += 1
            print(f"  [FAIL] {name} {detail}")


def unit_tests() -> int:
    print("=== 单测（离线断言）===")
    checker = Checker()

    # ---- A-R1：渲染结果长度恒为 1
    messages = [
        ChatMessage(role="system", content="你是助手"),
        ChatMessage(role="user", content="北京天气"),
        ChatMessage(role="assistant", content=None, tool_calls=to_tool_calls(
            [{"name": "get_weather", "arguments": {"city": "北京"}}])),
        ChatMessage(role="tool", content='{"temp":25}', tool_call_id="call_x"),
        ChatMessage(role="user", content="那上海呢"),
    ]
    rendered = render_messages(messages, [{"name": "get_weather", "description": "", "parameters": {}}])
    checker.check("A-R1 渲染长度恒为 1", len(rendered) == 1, f"实际 {len(rendered)}")
    text = rendered[0].content or ""
    checker.check("A-R1 历史被完整压平",
                  all(k in text for k in ("北京天气", "get_weather", "call_x", '{"temp":25}', "那上海呢")),
                  text[:200])
    checker.check("A-R1 current_user 不重复历史", text.count("北京天气") == 1)

    # ---- A-R2：输出不含 None
    checker.check("A-R2 content 非 None", isinstance(rendered[0].content, str))
    checker.check("A-R2 无字面量 None", "None" not in text)

    # ---- A-R3：tool_choice=none 不注入工具段
    plain = render_messages([ChatMessage(role="user", content="你好")],
                            [{"name": "get_weather", "description": "", "parameters": {}}],
                            tool_choice="none")
    checker.check("A-R3 tool_choice=none 无 <tools> 段", "<tools>" not in (plain[0].content or ""))

    # ---- A-P1：跨 chunk 暴力切分
    payload = '好的<tool_call>{"name": "get_weather", "arguments": {"city": "北京"}}</tool_call>完成'
    reference = None
    all_ok = True
    detail = ""
    for cut in range(0, len(payload) + 1):
        parser = ToolCallStreamParser()
        events = []
        events += parser.feed(payload[:cut])
        events += parser.feed(payload[cut:])
        events += parser.finish()
        # 契约是「聚合结果一致」：SSE 分片边界不是契约的一部分
        signature = (
            "".join(e.text for e in events if e.kind is EventKind.CONTENT),
            [e.call for e in events if e.kind is EventKind.TOOL_CALL],
        )
        if reference is None:
            reference = signature
        elif signature != reference:
            all_ok = False
            detail = f"cut={cut} -> {signature} != {reference}"
            break
    checker.check("A-P1 任意切点聚合结果一致", all_ok, detail)
    if reference:
        checker.check("A-P1 解析出 1 个工具调用", len(reference[1]) == 1, str(reference[1]))
        checker.check("A-P1 正文完整", reference[0] == "好的完成", reference[0])

    # ---- A-P2：畸形 JSON 降级为正文
    parser = ToolCallStreamParser()
    events = parser.feed('<tool_call>{不是json}</tool_call>')
    events += parser.finish()
    checker.check("A-P2 畸形 JSON 降级为 CONTENT",
                  any(e.kind is EventKind.CONTENT for e in events)
                  and not parser.has_tool_calls())
    checker.check("A-P2 未抛异常且状态复位", parser.state is ParseState.NORMAL)

    # ---- A-P3：缺闭合标签
    parser = ToolCallStreamParser()
    events = parser.feed('<tool_call>{"name": "get_weather", "arguments": {"city": "北京"}}')
    events += parser.finish()
    checker.check("A-P3 缺闭合标签仍能解析",
                  parser.has_tool_calls() and len(parser._calls) == 1)

    parser = ToolCallStreamParser()
    events = parser.feed("<tool_call>半个json")
    events += parser.finish()
    checker.check("A-P3 缺闭合且不可解析 → 原文降级",
                  not parser.has_tool_calls()
                  and any(TOOL_CALL_OPEN in e.text for e in events))

    # ---- A-P4：快路径零延迟
    parser = ToolCallStreamParser()
    events = parser.feed("你好")
    checker.check("A-P4 纯文本首 chunk 立即产出", any(e.kind is EventKind.CONTENT for e in events),
                  str(events))

    # ---- A-P5：多工具状态可逆
    parser = ToolCallStreamParser()
    events = parser.feed('<tool_call>{"name": "a", "arguments": {}}</tool_call>')
    events += parser.feed('中间<tool_call>{"name": "b", "arguments": {}}</tool_call>')
    events += parser.finish()
    calls = [e.call for e in events if e.kind is EventKind.TOOL_CALL]
    checker.check("A-P5 两个工具调用均被识别",
                  len(calls) == 2 and {c["name"] for c in calls} == {"a", "b"}, str(calls))

    # ---- extract 三格式
    content, calls = extract('<tool_call>{"name": "get_weather", "arguments": {"city": "北京"}}</tool_call>')
    checker.check("F1 整包解析", len(calls) == 1 and content == "", f"{content!r} {calls}")

    content, calls = extract('```json\n{"name": "get_weather", "arguments": {"city": "北京"}}\n```')
    checker.check("F2 围栏解析", len(calls) == 1 and content == "", f"{content!r} {calls}")

    content, calls = extract('{"name": "get_weather", "arguments": {"city": "北京"}}')
    checker.check("F3 裸 JSON 解析", len(calls) == 1 and content == "", f"{content!r} {calls}")

    content, calls = extract("今天北京晴，25 度。")
    checker.check("普通文本不误判", not calls and content == "今天北京晴，25 度。", f"{content!r}")

    # ---- F4：XML 形态（fixture 取自 2026-09-11 真机复现：13 工具链下模型退出 JSON 格式）
    xml_sample = (
        '<tool_call>\n<invoke name="web_search">\n'
        '<parameter name="query" string="true">苏州明天天气预报</parameter>\n'
        '<parameter name="max_results" string="false">5</parameter>\n'
        "</invoke>\n</tool_call>"
    )
    content, calls = extract(xml_sample)
    checker.check("F4 XML invoke 整包解析",
                  len(calls) == 1 and calls[0]["name"] == "web_search", f"{content!r} {calls}")
    checker.check("F4 按 string 属性还原参数类型",
                  bool(calls)
                  and calls[0]["arguments"].get("query") == "苏州明天天气预报"
                  and isinstance(calls[0]["arguments"].get("max_results"), int)
                  and calls[0]["arguments"]["max_results"] == 5,
                  str(calls))
    checker.check("F4 正文已剥离干净", content == "", repr(content))

    parser = ToolCallStreamParser()
    events = []
    for start in range(0, len(xml_sample), 7):
        events += parser.feed(xml_sample[start:start + 7])
    events += parser.finish()
    stream_calls = [e.call for e in events if e.kind is EventKind.TOOL_CALL]
    checker.check("F4 流式分片解析",
                  len(stream_calls) == 1 and stream_calls[0]["name"] == "web_search",
                  str(stream_calls))

    multi = ('<tool_call><invoke name="a"><parameter name="x" string="true">1</parameter></invoke>'
             '<invoke name="b"><parameter name="y" string="false">2</parameter></invoke></tool_call>')
    content, calls = extract(multi)
    checker.check("F4 一个外壳含多个 invoke",
                  [call["name"] for call in calls] == ["a", "b"], str(calls))

    content, calls = extract(
        '<invoke name="web_search"><parameter name="query" string="true">q</parameter></invoke>')
    checker.check("F4 无外壳裸 invoke 兜底",
                  len(calls) == 1 and calls[0]["name"] == "web_search", str(calls))

    # ---- F5：DSML 标记（真机抓取样本 + 首轮事件里的字面量前缀形态）
    bar = chr(0xFF5C) * 2
    newline = chr(10)
    mark = bar + "DSML" + bar

    incident = (
        "<tool" + mark + " calls>" + newline
        + "<tool" + mark + ' invoke name="web_search">' + newline
        + "<tool" + mark + ' parameter name="query" string="true">苏州明天天气预报</tool' + mark + " parameter>" + newline
        + "</tool" + mark + " invoke>" + newline
        + "</tool" + mark + " calls>"
    )
    content, calls = extract(incident)
    checker.check("F5 字面量前缀形态解析",
                  len(calls) == 1 and calls[0]["name"] == "web_search", str(calls))
    checker.check("F5 参数值正确",
                  bool(calls) and calls[0]["arguments"].get("query") == "苏州明天天气预报",
                  str(calls))
    checker.check("F5 正文无残留标记", content == "", repr(content))

    bare = (
        "<" + mark + " calls>" + newline
        + "<" + mark + ' invoke name="web_search">' + newline
        + "<" + mark + ' parameter name="max_results" string="false">5</' + mark + " parameter>" + newline
        + "</" + mark + " invoke>" + newline
        + "</" + mark + " calls>"
    )
    content, calls = extract(bare)
    checker.check("F5 无字面量前缀形态解析",
                  len(calls) == 1 and calls[0]["name"] == "web_search", str(calls))
    checker.check("F5 非字符串参数还原类型",
                  bool(calls) and calls[0]["arguments"].get("max_results") == 5
                  and isinstance(calls[0]["arguments"]["max_results"], int),
                  str(calls))

    for label, sample in (("字面量前缀形态", incident), ("无字面量前缀形态", bare)):
        content, calls = extract(sample)
        checker.check(f"F5 {label}整包解析",
                      len(calls) == 1 and calls[0]["name"] == "web_search", str(calls))
        for size in (1, 2, 5):
            stream_parser = ToolCallStreamParser()
            stream_events = []
            for start in range(0, len(sample), size):
                stream_events += stream_parser.feed(sample[start:start + size])
            stream_events += stream_parser.finish()
            got = [e.call for e in stream_events if e.kind is EventKind.TOOL_CALL]
            checker.check(f"F5 {label}流式分片({size}字)解析",
                          len(got) == 1 and got[0]["name"] == "web_search", str(got))
            leaked = "".join(e.text or "" for e in stream_events if e.kind is EventKind.CONTENT)
            checker.check(f"F5 {label}分片({size}字)无标记泄漏",
                          not leaked.strip(), repr(leaked[:80]))

    # ---- to_tool_calls 封装
    built = to_tool_calls([{"name": "get_weather", "arguments": {"city": "北京"}}])
    checker.check("arguments 封装为 JSON 字符串",
                  len(built) == 1 and isinstance(built[0].function.arguments, str)
                  and json.loads(built[0].function.arguments)["city"] == "北京")
    checker.check("tool_call id 前缀正确", built[0].id.startswith("call_"))

    # ---- prepare：tool_choice=none 时关闭解析
    prepared = prepare(make_request("m", [ChatMessage(role="user", content="你好")],
                                    tool_defs(WEATHER_TOOL), tool_choice="none"))
    checker.check("prepare: tool_choice=none → parse_enabled=False", prepared.parse_enabled is False)
    prepared = prepare(make_request("m", [ChatMessage(role="user", content="你好")],
                                    tool_defs(WEATHER_TOOL)))
    checker.check("prepare: tools 路径 parse_enabled=True", prepared.parse_enabled is True)
    checker.check("prepare: 透传给 provider 的消息长度为 1", len(prepared.request.messages) == 1)

    print(f"\n单测结果：{checker.passed} 通过 / {checker.failed} 失败")
    return checker.failed


# ================================================================ L 维度


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(base: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/v1/models", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def call(client: httpx.Client, base: str, payload: dict, timeout: float):
    response = client.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}: {response.text[:120]}"
    try:
        return response.json(), ""
    except Exception as exc:
        return None, f"响应解析失败: {exc}"


def cases_for(model: str) -> list:
    """返回 [(用例名, payload, 判定函数, 是否计入门槛)]"""
    weather = tool_defs(WEATHER_TOOL)
    both = tool_defs(WEATHER_TOOL, TIME_TOOL)

    def base(messages, tools):
        return {"model": model, "messages": messages, "tools": [
            t.model_dump() for t in tools], "stream": False}

    def brief(data):
        message = data["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        return f"calls={[c['function']['name'] for c in calls]} content={content[:70]!r}"

    def u1(data):
        calls = data["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            return False, brief(data)
        if calls[0]["function"]["name"] != "get_weather":
            return False, brief(data)
        try:
            args = json.loads(calls[0]["function"]["arguments"])
        except Exception:
            return False, f"arguments 非法 JSON | {brief(data)}"
        return ("北京" in str(args.get("city", ""))), f"args={args}"

    def u2(data):
        calls = data["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            return False, brief(data)
        if calls[0]["function"]["name"] != "get_weather":
            return False, f"选错工具 | {brief(data)}"
        try:
            args = json.loads(calls[0]["function"]["arguments"])
        except Exception:
            return False, f"arguments 非法 JSON | {brief(data)}"
        return ("上海" in str(args.get("city", ""))), brief(data)

    def u3(data):
        calls = data["choices"][0]["message"].get("tool_calls") or []
        content = data["choices"][0]["message"].get("content") or ""
        return ((not calls) and bool(content.strip())), brief(data)

    def u4(data):
        calls = data["choices"][0]["message"].get("tool_calls") or []
        return (len(calls) >= 2), f"并行调用数={len(calls)} | {brief(data)}"

    def u5(data):
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        return ("25" in content), brief(data)

    tool_call_stub = {
        "id": "call_test1", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
    }

    return [
        ("U1 单工具+中文触发", base([{"role": "user", "content": "北京今天天气怎么样？"}], weather), u1, True),
        # U2 设计要点：必须「信息不可自答」+「参数齐备」，否则模型会反问或直接作答，
        # 那就变成在用例歧义，而不是在测工具选择能力
        ("U2 多工具需选择", base([{"role": "user", "content": "帮我查一下上海现在的天气"}], both), u2, True),
        ("U3 诱导(无需工具)", base([{"role": "user", "content": "1+1 等于几？只回复数字。"}], weather), u3, True),
        ("U4 需并行调用", base([{"role": "user", "content": "帮我查一下北京和上海的天气"}], weather), u4, False),
        ("U5 工具结果回传后继续", base([
            {"role": "user", "content": "北京今天天气怎么样？"},
            {"role": "assistant", "content": None, "tool_calls": [tool_call_stub]},
            {"role": "tool", "tool_call_id": "call_test1", "content": '{"city": "北京", "temp": 25, "desc": "晴"}'},
        ], weather), u5, True),
    ]


def llm_tests(platforms: list, timeout: float) -> int:
    from src.config import settings
    from src.providers import DeepSeekProvider, KimiProvider, DoubaoProvider, ZhipuProvider, MiniMaxProvider

    class_map = {
        "deepseek": DeepSeekProvider, "kimi": KimiProvider, "doubao": DoubaoProvider,
        "zhipu": ZhipuProvider, "minimax": MiniMaxProvider,
    }
    default_model = {
        "deepseek": "deepseek-flash", "kimi": "kimi-k3", "doubao": "doubao-seed-2.1-pro",
        "zhipu": "glm-5.3-flash", "minimax": "MiniMax-M3",
    }

    available = [p for p in platforms
                 if p in class_map and getattr(settings.providers, p).token]
    if not available:
        print("没有可用平台（未配置 Token）")
        return 0

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    print("\n=== L 维度（真实请求，门槛 >= 80%）===")
    failures = 0
    try:
        if not wait_ready(base):
            print("服务启动失败")
            return 1
        with httpx.Client() as client:
            for platform in available:
                model = default_model[platform]
                print(f"\n--- {platform} / {model} ---")
                gated_pass = gated_total = 0
                for name, payload, judge, gated in cases_for(model):
                    data, error = call(client, base, payload, timeout)
                    if data is None:
                        ok, detail = False, error
                    else:
                        try:
                            ok, detail = judge(data)
                        except Exception as exc:
                            ok, detail = False, f"判定异常 {exc}"
                    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
                    if gated:
                        gated_total += 1
                        gated_pass += 1 if ok else 0
                    elif not ok:
                        print(f"         （P4 已知限制，不计入门槛）")
                rate = (gated_pass / gated_total * 100) if gated_total else 0.0
                verdict = "✅" if rate >= 80 else "❌"
                print(f"  成功率：{gated_pass}/{gated_total} = {rate:.0f}%  {verdict}")
                if rate < 80:
                    failures += 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()

    print(f"\nL 维度未达标平台数：{failures}")
    return failures


def soak_tests(platform: str, iterations: int, timeout: float) -> int:
    """I5：连续混合请求，验证无请求级状态泄漏"""
    from src.config import settings
    from src.providers import DeepSeekProvider, KimiProvider, DoubaoProvider

    class_map = {"deepseek": DeepSeekProvider, "kimi": KimiProvider, "doubao": DoubaoProvider}
    default_model = {"deepseek": "deepseek-flash", "kimi": "kimi-k3",
                     "doubao": "doubao-seed-2.1-pro"}
    if platform not in class_map or not getattr(settings.providers, platform).token:
        print(f"平台 {platform} 不可用，跳过 I5")
        return 0

    model = default_model[platform]
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    print(f"\n=== I5 无状态泄漏（{platform}，交替 {iterations} 次混合请求）===")
    failures = 0
    try:
        if not wait_ready(base):
            print("服务启动失败")
            return 1
        tools_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
            "tools": [WEATHER_TOOL], "stream": False,
        }
        plain_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "只回复两个字：正常"}],
            "stream": False,
        }
        tool_hits = plain_hits = 0
        tool_total = iterations // 2 + iterations % 2
        plain_total = iterations // 2
        with httpx.Client() as client:
            for index in range(iterations):
                payload = tools_payload if index % 2 == 0 else plain_payload
                data, error = call(client, base, payload, timeout)
                if data is None:
                    failures += 1
                    print(f"  [FAIL] 第 {index + 1} 次：{error}")
                    continue
                message = data["choices"][0]["message"]
                calls = message.get("tool_calls") or []
                if index % 2 == 0:
                    if calls and calls[0]["function"]["name"] == "get_weather":
                        tool_hits += 1
                    else:
                        failures += 1
                        print(f"  [FAIL] 第 {index + 1} 次(tools)：{message}")
                else:
                    if (message.get("content") or "").strip():
                        plain_hits += 1
                    else:
                        failures += 1
                        print(f"  [FAIL] 第 {index + 1} 次(plain)：空内容")

            data, error = call(client, base, tools_payload, timeout)
            last_ok = bool(data and (data["choices"][0]["message"].get("tool_calls") or []))
            print(f"  tools 命中 {tool_hits}/{tool_total} | plain 命中 {plain_hits}/{plain_total}"
                  f" | 末次复验 {'OK' if last_ok else 'FAIL'}")
            if not last_ok:
                failures += 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()

    print(f"  I5 失败项：{failures}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="工具调用适配层验证")
    parser.add_argument("--llm", action="store_true", help="追加 L 维度真实请求验证")
    parser.add_argument("--soak", type=int, default=0, help="I5：混合请求次数（建议 100）")
    parser.add_argument("--soak-platform", default="deepseek")
    parser.add_argument("--unit-only", action="store_true", help="只跑单测")
    parser.add_argument("--platforms", nargs="*", default=["deepseek", "kimi", "doubao"])
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    unit_failures = unit_tests()
    llm_failures = soak_failures = 0
    if args.llm and not args.unit_only:
        llm_failures = llm_tests(args.platforms, args.timeout)
    if args.soak and not args.unit_only:
        soak_failures = soak_tests(args.soak_platform, args.soak, args.timeout)

    print("\n=== 汇总 ===")
    print(f"单测失败：{unit_failures} | L 维度未达标平台：{llm_failures} | I5 失败项：{soak_failures}")
    return 1 if (unit_failures or llm_failures or soak_failures) else 0


if __name__ == "__main__":
    sys.exit(main())
