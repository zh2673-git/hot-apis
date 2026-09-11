"""每平台控频器：串行排队 + 强制最小间隔。

Web 逆向通道没有公开的配额语义，实测同一平台高频连续调用（约 12 次起）会触发
上游限流乃至**临时禁言账号**（2026-09-11 实测出现「账号已被禁言至次日 11:02」）。
因此 relay 在出口做「每平台一闸」：

- 同平台的并发请求在锁上排队（请求启动时刻按间隔拉开，不阻塞上游响应过程）
- 两次请求的启动时刻强制间隔 `min_interval_ms`，可按平台覆盖

配置：
    config.yaml -> rate_limit.min_interval_ms / rate_limit.overrides.<platform>
    环境变量 RATE_LIMIT_MS 覆盖全局默认；0 = 关闭控频（风险自负）。
"""

import asyncio
import os
import time
from typing import Dict


class RateLimiter:
    """每平台一个队列：先到先得，出闸间隔不小于配置值"""

    def __init__(self, default_interval_ms: float, per_provider_ms: Dict[str, float] = None) -> None:
        self._default = max(0.0, float(default_interval_ms)) / 1000.0
        self._per = {k: max(0.0, float(v)) / 1000.0 for k, v in (per_provider_ms or {}).items()}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._next_ok: Dict[str, float] = {}

    def interval(self, name: str) -> float:
        return self._per.get(name, self._default)

    async def acquire(self, name: str) -> None:
        """放行一次请求；同平台并发时按到达顺序排队并拉开间隔"""
        interval = self.interval(name)
        if interval <= 0:
            return
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            wait = self._next_ok.get(name, 0.0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_ok[name] = time.monotonic() + interval


def from_settings(min_interval_ms: float, overrides: Dict[str, float] = None) -> RateLimiter:
    return RateLimiter(min_interval_ms, overrides)


if __name__ == "__main__":
    # 自测：间隔生效 / 关闭生效 / 并发排队
    async def _self_test() -> None:
        limiter = RateLimiter(200.0)
        start = time.monotonic()
        for _ in range(3):
            await limiter.acquire("a")
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms >= 400, f"间隔未生效: {elapsed_ms:.0f}ms"
        print(f"间隔生效: 3 次请求耗时 {elapsed_ms:.0f}ms (期望 >= 400ms)")

        free = RateLimiter(0.0)
        t0 = time.monotonic()
        for _ in range(3):
            await free.acquire("a")
        assert (time.monotonic() - t0) < 0.05, "关闭控频后不应等待"
        print("关闭生效: 0 间隔立即放行")

        overlapped = RateLimiter(150.0)
        t0 = time.monotonic()
        await asyncio.gather(*(overlapped.acquire("x") for _ in range(4)))
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms >= 450, f"并发未排队: {elapsed_ms:.0f}ms"
        print(f"并发排队: 4 个并发请求耗时 {elapsed_ms:.0f}ms (期望 >= 450ms)")

        print("ratelimit self-test OK")

    asyncio.run(_self_test())
