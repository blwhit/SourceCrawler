import logging
import re
from typing import AsyncIterator

import httpx

from scanners.base import BaseScanner
from core.models import SourceResult, SearchMode

logger = logging.getLogger(__name__)


class SerperScanner(BaseScanner):
    name = "serper"

    def is_configured(self) -> bool:
        return bool(self.config.get("serper", {}).get("api_key"))

    async def scan(self, query: str, mode: SearchMode) -> AsyncIterator[SourceResult]:
        api_key = self.config["serper"]["api_key"]
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }

        search_term = query
        regex_pattern = None
        if mode == SearchMode.REGEX:
            # Google doesn't support regex - strip metacharacters for dork query
            search_term = re.sub(r'[.*+?^${}()|[\]\\]', '', query).strip()
            if not search_term:
                logger.warning("[serper] Regex reduced to empty string, skipping")
                return
            try:
                regex_pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                logger.warning(f"[serper] Invalid regex pattern: {query}")
                return

        dork_queries = [
            f'intext:"{search_term}" ext:js',
            f'intext:"{search_term}" ext:html',
            f'intext:"{search_term}" ext:py',
            f'intext:"{search_term}" ext:php',
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for dork in dork_queries:
                if self.is_cancelled:
                    return

                payload = {"q": dork, "num": 30}
                resp = await self._rate_limited_post(
                    client,
                    "https://google.serper.dev/search",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.error(f"[serper] HTTP {resp.status_code}: {resp.text[:200]}")
                    continue

                data = resp.json()
                for item in data.get("organic", []):
                    if self.is_cancelled:
                        return
                    # Post-filter with regex if in regex mode
                    if regex_pattern:
                        snippet = item.get("snippet", "")
                        title = item.get("title", "")
                        if not (regex_pattern.search(snippet) or regex_pattern.search(title)):
                            continue
                    yield SourceResult(
                        provider_name=self.name,
                        target_url=item.get("link", ""),
                        code_snippet=item.get("snippet", ""),
                        metadata={
                            "title": item.get("title", ""),
                            "position": item.get("position", 0),
                            "dork_query": dork,
                        },
                    )
