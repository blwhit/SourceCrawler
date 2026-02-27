import logging
from typing import AsyncIterator

import httpx

from scanners.base import BaseScanner
from core.models import SourceResult, SearchMode

logger = logging.getLogger(__name__)


class UrlscanScanner(BaseScanner):
    name = "urlscan"

    def is_configured(self) -> bool:
        return bool(self.config.get("urlscan", {}).get("api_key"))

    async def scan(self, query: str, mode: SearchMode) -> AsyncIterator[SourceResult]:
        api_key = self.config["urlscan"]["api_key"]
        headers = {"API-Key": api_key}

        if mode == SearchMode.REGEX:
            es_query = f'page.url:/{query}/ OR page.domain:/{query}/'
        else:
            es_query = f'page.url:"{query}" OR page.domain:"{query}"'

        search_after = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(10):
                if self.is_cancelled:
                    return

                params = {"q": es_query, "size": 100}
                if search_after:
                    params["search_after"] = search_after

                resp = await self._rate_limited_get(
                    client,
                    "https://urlscan.io/api/v1/search/",
                    headers=headers,
                    params=params,
                )
                if resp.status_code != 200:
                    logger.error(f"[urlscan] HTTP {resp.status_code}: {resp.text[:200]}")
                    return

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return

                for item in results:
                    if self.is_cancelled:
                        return
                    page_info = item.get("page", {})
                    task_info = item.get("task", {})
                    yield SourceResult(
                        provider_name=self.name,
                        target_url=page_info.get("url", ""),
                        code_snippet=f"[urlscan] {page_info.get('title', 'N/A')}",
                        metadata={
                            "domain": page_info.get("domain", ""),
                            "ip": page_info.get("ip", ""),
                            "country": page_info.get("country", ""),
                            "scan_id": item.get("_id", ""),
                            "scan_url": f"https://urlscan.io/result/{item.get('_id', '')}/",
                            "visibility": task_info.get("visibility", ""),
                        },
                    )

                if not data.get("has_more"):
                    return
                sort_vals = results[-1].get("sort")
                if not sort_vals:
                    return
                search_after = ",".join(str(s) for s in sort_vals)
