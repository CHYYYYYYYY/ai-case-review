"""令牌桶限流器.

控制对 LLM API 的并发与 QPS, 避免触发阿里云限流.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class TokenBucket:
    """令牌桶: 每秒补充 qps 个令牌, 调用前先取 1 个."""

    def __init__(self, qps: int, capacity: int | None = None):
        self.qps = qps
        self.capacity = capacity or qps
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.capacity, self._tokens + elapsed * self.qps)
            self._last = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.qps
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


class LimiterRegistry:
    """按 provider 维护独立限流器."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def get(self, provider: str, qps: int) -> TokenBucket:
        if provider not in self._buckets:
            self._buckets[provider] = TokenBucket(qps=qps)
        return self._buckets[provider]


_limiter_registry = LimiterRegistry()


def get_limiter(provider: str, qps: int) -> TokenBucket:
    return _limiter_registry.get(provider, qps)
