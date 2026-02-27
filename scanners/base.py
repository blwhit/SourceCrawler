import abc
import asyncio
import logging
from typing import AsyncIterator

import httpx

from core.models import SourceResult, SearchMode
from core.rate_limiter import RateLimitRegistry

logger = logging.getLogger(__name__)


class BaseScanner(abc.ABC):
    """Base class for all source code search scanners."""

    name: str = "base"

    def __init__(self, rate_limiter: RateLimitRegistry, config: dict):
        self.rate_limiter = rate_limiter
        self.config = config
        self._cancelled = asyncio.Event()

    @abc.abstractmethod
    async def scan(self, query: str, mode: SearchMode) -> AsyncIterator[SourceResult]:
        """Yield SourceResult objects as they are found."""
        yield  # type: ignore
        ...

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def is_configured(self) -> bool:
        """Return True if this scanner has the credentials it needs."""
        return True

    async def _rate_limited_get(self, client: httpx.AsyncClient,
                                url: str, **kwargs) -> httpx.Response:
        """GET with rate limiting and 429 retry."""
        await self.rate_limiter.acquire(self.name)
        response = await client.get(url, **kwargs)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 60))
            logger.warning(f"[{self.name}] 429 received, cooling down {retry_after}s")
            self.rate_limiter.report_429(self.name, retry_after)
            await asyncio.sleep(min(retry_after, 120))
            return await self._rate_limited_get(client, url, **kwargs)
        return response

    async def _rate_limited_post(self, client: httpx.AsyncClient,
                                 url: str, **kwargs) -> httpx.Response:
        """POST with rate limiting and 429 retry."""
        await self.rate_limiter.acquire(self.name)
        response = await client.post(url, **kwargs)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 60))
            logger.warning(f"[{self.name}] 429 received, cooling down {retry_after}s")
            self.rate_limiter.report_429(self.name, retry_after)
            await asyncio.sleep(min(retry_after, 120))
            return await self._rate_limited_post(client, url, **kwargs)
        return response
