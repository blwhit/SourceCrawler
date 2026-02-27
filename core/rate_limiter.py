import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Token bucket rate limiter for a single provider."""
    rate: float
    max_tokens: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    cooldown_until: float = 0.0

    def __post_init__(self):
        self.tokens = self.max_tokens
        self.last_refill = time.monotonic()

    async def acquire(self) -> None:
        """Wait until a token is available, respecting cooldown."""
        async with self._lock:
            now = time.time()
            if now < self.cooldown_until:
                wait = self.cooldown_until - now
                await asyncio.sleep(wait)

            current = time.monotonic()
            elapsed = current - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
            self.last_refill = current

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1

    def set_cooldown(self, seconds: float) -> None:
        """Called when a 429 is received."""
        self.cooldown_until = time.time() + seconds


class RateLimitRegistry:
    """Global registry of per-provider rate limiters."""

    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {}

    def register(self, provider: str, requests_per_minute: float) -> None:
        rate = requests_per_minute / 60.0
        self._buckets[provider] = TokenBucket(rate=rate, max_tokens=max(rate * 10, 2.0))

    def get(self, provider: str) -> TokenBucket:
        return self._buckets[provider]

    async def acquire(self, provider: str) -> None:
        if provider in self._buckets:
            await self._buckets[provider].acquire()

    def report_429(self, provider: str, retry_after: float = 60.0) -> None:
        if provider in self._buckets:
            self._buckets[provider].set_cooldown(retry_after)
