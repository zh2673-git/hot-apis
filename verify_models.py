"""模型可用性实测脚本

用途：启动真实 uvicorn 服务，通过 HTTP 逐模型验证「路由分发 -> Token 认证
-> 平台内部编码映射 -> 上游响应解析」整条链路是否打通。

为什么用真实服务而不是 TestClient：TestClient 每个请求会新建事件循环，而 provider
缓存了绑定旧事件循环的 httpx client，造成 "Event loop is closed" 假失败。

Token 配置：
    1. 复制 .env.example 为 .env（或用 grab_tokens.py 自动填充）
    2. 不需要的平台留空会自动跳过
    3. Token 仅存放在本地 .env，该文件已被 .gitignore 忽略

用法：
    python verify_models.py                # 仅验证本轮新增/变更的模型
    python verify_models.py kimi qwen      # 只验证指定平台
    python verify_models.py --all          # 验证各平台 models 列表中的全部模型
    python verify_models.py --stream       # 追加流式（SSE）接口验证
    python verify_models.py --timeout 180  # 单请求超时（秒），默认 120

退出码：0 = 全部通过或未配置 Token；1 = 存在失败项。
"""

import argparse
import os
import socket
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from src.config import settings
from src.providers import (
    DeepSeekProvider,
    KimiProvider,
    MetasoProvider,
    DoubaoProvider,
    QwenProvider,
    ZhipuProvider,
    MiniMaxProvider,
)

PROVIDERS = {
    "deepseek": DeepSeekProvider,
    "kimi": KimiProvider,
    "metaso": MetasoProvider,
    "doubao": DoubaoProvider,
    "qwen": QwenProvider,
    "zhipu": ZhipuProvider,
    "minimax": MiniMaxProvider,
}

# 本轮新增/变更的模型（用于快速回归，--all 时忽略）
CHANGED_MODELS = {
    "deepseek": ["deepseek-flash"],
    "kimi": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"],
    "metaso": ["metaso-deep-research"],
    "doubao": ["doubao-seed-2.1-pro", "doubao-seed-2.1-turbo", "doubao-seed-2.0-pro"],
    "qwen": ["qwen3.7-max", "qwen3.7-plus"],
    "zhipu": ["glm-5.3", "glm-5.3-flash"],
    "minimax": ["MiniMax-M3"],
}

PROMPT = "只回复两个字：正常"


def has_token(platform: str) -> bool:
    return bool(getattr(settings.providers, platform).token)


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


def check_once(client: httpx.Client, base: str, model: str, timeout: float) -> tuple:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "max_tokens": 32,
    }
    start = time.time()
    try:
        resp = client.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
    except Exception as exc:
        return "ERR", time.time() - start, f"{type(exc).__name__}: {exc}"

    elapsed = time.time() - start
    if resp.status_code != 200:
        return "FAIL", elapsed, f"HTTP {resp.status_code} | {resp.text[:180]}"
    try:
        content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return "FAIL", elapsed, f"响应结构异常 | {resp.text[:180]}"
    if not content:
        return "EMPTY", elapsed, "(上游返回空内容)"
    return "OK", elapsed, content.replace("\n", " ")[:60]


def check_stream(client: httpx.Client, base: str, model: str, timeout: float) -> tuple:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 32,
    }
    start = time.time()
    try:
        with client.stream("POST", f"{base}/v1/chat/completions", json=payload, timeout=timeout) as resp:
            if resp.status_code != 200:
                return "FAIL", time.time() - start, f"HTTP {resp.status_code} | {resp.read()[:150]}"
            chunks = 0
            got_done = False
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    got_done = True
                    break
                if '"error"' in body:
                    return "FAIL", time.time() - start, body[:180]
                chunks += 1
                if chunks >= 500:
                    break
        elapsed = time.time() - start
        if not got_done:
            return "PART", elapsed, f"未收到 [DONE]，已收 {chunks} 个 chunk"
        return "OK", elapsed, f"{chunks} 个 chunk 后正常 [DONE]"
    except Exception as exc:
        return "ERR", time.time() - start, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="模型可用性实测")
    parser.add_argument("platforms", nargs="*", help="要验证的平台，缺省为全部")
    parser.add_argument("--all", action="store_true", help="验证各平台 models 列表中的全部模型")
    parser.add_argument("--models", nargs="*", help="只验证指定模型名（跨平台，用于对照实验）")
    parser.add_argument("--stream", action="store_true", help="追加流式接口验证")
    parser.add_argument("--timeout", type=float, default=120.0, help="单请求超时（秒）")
    args = parser.parse_args()

    targets = args.platforms or list(PROVIDERS.keys())
    unknown = [p for p in targets if p not in PROVIDERS]
    if unknown:
        print(f"未知平台：{', '.join(unknown)}（可选：{', '.join(PROVIDERS.keys())}）")
        return 1

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=HERE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    failures = 0
    skipped = []
    try:
        if not wait_ready(base):
            print("服务启动失败，uvicorn 输出：")
            print((server.stderr.read() or b"").decode("utf-8", "ignore")[:800])
            return 1
        print(f"服务已就绪: {base}")

        with httpx.Client() as client:
            for platform in targets:
                if not has_token(platform):
                    skipped.append(platform)
                    continue

                declared = list(PROVIDERS[platform](token="x").models)
                if args.models:
                    models = [m for m in args.models if m in declared]
                elif args.all:
                    models = declared
                else:
                    models = CHANGED_MODELS[platform]
                if not models:
                    continue
                print(f"\n=== {platform} ({len(models)} 个模型) ===")
                for model in models:
                    status, elapsed, detail = check_once(client, base, model, args.timeout)
                    print(f"  [{status:<5}] {model:<32} {elapsed:6.1f}s  {detail}")
                    if status in ("FAIL", "ERR"):
                        failures += 1
                    if args.stream:
                        s_status, s_elapsed, s_detail = check_stream(client, base, model, args.timeout)
                        print(f"  [S:{s_status:<3}] {model:<32} {s_elapsed:6.1f}s  {s_detail}")
                        if s_status in ("FAIL", "ERR"):
                            failures += 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()

    print("\n=== 汇总 ===")
    print(f"已跳过（未配置 Token）：{', '.join(skipped) if skipped else '无'}")
    print(f"失败项：{failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
