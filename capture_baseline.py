"""P3 基线快照 / I1 回归比对

用途：记录「不传 tools」时的响应**结构**（改造前录制），作为 I1「逐字段等价」的比对基准。

为什么需要归一化：模型每次生成的 `content` 文本天然不同，直接比文本值必然失败。
本脚本在比对前把**生成性/易变字段**替换为占位符，只比较：
  - 字段集合（哪些 key 存在）
  - 值类型骨架
  - 非生成性字段值

用法：
    python capture_baseline.py --record            # 改造前/后录制基线
    python capture_baseline.py --compare           # 改造后与基线比对（I1）
    python capture_baseline.py --record --platforms deepseek
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

OUT_DIR = os.path.join(HERE, "docs", "baseline")

# 平台 -> 用于基线的模型（挑延迟最低的，避免录制过程过长）
DEFAULT_MODELS = {
    "deepseek": "deepseek-flash",
    "kimi": "kimi-k3",
    "doubao": "doubao-seed-2.1-pro",
    "zhipu": "glm-5.3-flash",
    "minimax": "MiniMax-M3",
}

# 归一化时替换为占位符的字段（生成性 / 每次不同）
VOLATILE_KEYS = {"content", "id", "created", "usage", "prompt_tokens",
                 "completion_tokens", "total_tokens"}
PLACEHOLDER = "<TEXT>"


def normalize(node):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in VOLATILE_KEYS and not isinstance(value, (dict, list)):
                out[key] = PLACEHOLDER
            else:
                out[key] = normalize(value)
        return out
    if isinstance(node, list):
        return [normalize(i) for i in node]
    return node


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


def build_payload(model: str, stream: bool) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是简洁助手。"},
            {"role": "user", "content": "只回复两个字：正常"},
        ],
        "stream": stream,
        "max_tokens": 32,
    }


def has_token(platform: str) -> bool:
    from src.config import settings
    return bool(getattr(settings.providers, platform).token)


def record(client: httpx.Client, base: str, platform: str, model: str, timeout: float) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    record_data = {}

    resp = client.post(f"{base}/v1/chat/completions", json=build_payload(model, False), timeout=timeout)
    record_data["non_stream"] = {
        "status": resp.status_code,
        "raw": resp.text,
        "normalized": normalize(resp.json()) if resp.status_code == 200 else None,
    }

    chunks = []
    with client.stream("POST", f"{base}/v1/chat/completions",
                       json=build_payload(model, True), timeout=timeout) as sresp:
        record_data["stream"] = {"status": sresp.status_code}
        for line in sresp.iter_lines():
            if line.startswith("data: "):
                chunks.append(line[6:].strip())
    record_data["stream"]["chunk_count"] = len(chunks)
    record_data["stream"]["normalized_frames"] = [
        "<DONE>" if c == "[DONE]" else normalize(json.loads(c))
        for c in chunks if c == "[DONE]" or c.startswith("{")
    ]

    path = os.path.join(OUT_DIR, f"{platform}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record_data, handle, ensure_ascii=False, indent=2)
    return record_data


def compare(platform: str, current: dict) -> tuple:
    path = os.path.join(OUT_DIR, f"{platform}.json")
    if not os.path.exists(path):
        return False, ["基线不存在"]
    with open(path, encoding="utf-8") as handle:
        baseline = json.load(handle)

    diffs = []
    if baseline["non_stream"]["normalized"] != current["non_stream"]["normalized"]:
        diffs.append("非流式：结构不一致")
        diffs.append("  baseline: " + json.dumps(baseline["non_stream"]["normalized"], ensure_ascii=False)[:400])
        diffs.append("  current : " + json.dumps(current["non_stream"]["normalized"], ensure_ascii=False)[:400])

    b_frames = baseline["stream"]["normalized_frames"]
    c_frames = current["stream"]["normalized_frames"]
    if b_frames and c_frames and b_frames[-1] != c_frames[-1]:
        diffs.append("流式：终止帧不一致")
    if (b_frames[0] if b_frames else None) != (c_frames[0] if c_frames else None):
        diffs.append("流式：首帧结构不一致")
        diffs.append("  baseline: " + json.dumps(b_frames[0] if b_frames else None, ensure_ascii=False)[:300])
        diffs.append("  current : " + json.dumps(c_frames[0] if c_frames else None, ensure_ascii=False)[:300])
    return (not diffs), diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="P3 基线快照 / I1 回归比对")
    parser.add_argument("--record", action="store_true", help="录制基线（覆盖已有）")
    parser.add_argument("--compare", action="store_true", help="与基线比对（I1）")
    parser.add_argument("--platforms", nargs="*", default=list(DEFAULT_MODELS.keys()))
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if not (args.record or args.compare):
        parser.error("请指定 --record 或 --compare")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    failures = 0
    try:
        if not wait_ready(base):
            print("服务启动失败")
            print((server.stderr.read() or b"").decode("utf-8", "ignore")[:500])
            return 1

        with httpx.Client() as client:
            for platform in args.platforms:
                if not has_token(platform):
                    print(f"[SKIP] {platform} 未配置 Token")
                    continue
                model = DEFAULT_MODELS[platform]
                print(f"\n=== {platform} / {model} ===")
                current = record(client, base, platform, model, args.timeout)
                print(f"  非流式状态: {current['non_stream']['status']}"
                      f" | 流式帧数: {current['stream']['chunk_count']}")
                print("  非流式结构: "
                      + json.dumps(current["non_stream"]["normalized"], ensure_ascii=False)[:300])
                if args.compare:
                    ok, diffs = compare(platform, current)
                    print(f"  I1 判定: {'✅ 等价' if ok else '❌ 不等价'}")
                    for line in diffs:
                        print("   ", line)
                    if not ok:
                        failures += 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()

    print(f"\n=== 汇总 ===\n{'录制完成' if args.record else '比对失败项: %d' % failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
