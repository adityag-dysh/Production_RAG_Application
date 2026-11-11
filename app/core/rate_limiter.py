"""Async token-bucket rate limiter for pacing outbound API calls (e.g. Gemini embeddings).
Refills continuously at rate/sec; callers await acquire() which blocks until enough budget exists."""
import asyncio
import time


class TokenBucket:
    def __init__(self, per_minute: float, capacity: float | None = None):
        self.rate = per_minute / 60.0                      # tokens added per second
        self.capacity = capacity if capacity is not None else per_minute
        self.tokens = self.capacity
        self._ts = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self._ts) * self.rate)
                self._ts = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                deficit = amount - self.tokens
            await asyncio.sleep(min(deficit / self.rate, 30.0))
