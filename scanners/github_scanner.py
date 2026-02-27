import logging
import re
from typing import AsyncIterator

import httpx

from scanners.base import BaseScanner
from core.models import SourceResult, SearchMode

logger = logging.getLogger(__name__)


class GitHubScanner(BaseScanner):
    name = "github"

    def is_configured(self) -> bool:
        return bool(self.config.get("github", {}).get("token"))

    async def scan(self, query: str, mode: SearchMode) -> AsyncIterator[SourceResult]:
        token = self.config["github"]["token"]
        headers = {
            "Accept": "application/vnd.github.text-match+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        search_query = query
        regex_pattern = None
        if mode == SearchMode.REGEX:
            regex_pattern = re.compile(query, re.IGNORECASE)
            search_query = re.sub(r'[.*+?^${}()|[\]\\]', '', query)
            if not search_query.strip():
                logger.warning("[github] Regex reduced to empty string after stripping metacharacters")
                return

        page = 1
        async with httpx.AsyncClient(timeout=30.0) as client:
            while not self.is_cancelled:
                params = {"q": search_query, "per_page": 30, "page": page}
                resp = await self._rate_limited_get(
                    client,
                    "https://api.github.com/search/code",
                    headers=headers,
                    params=params,
                )
                if resp.status_code != 200:
                    logger.error(f"[github] HTTP {resp.status_code}: {resp.text[:200]}")
                    return

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    return

                for item in items:
                    if self.is_cancelled:
                        return

                    snippet = ""
                    text_matches = item.get("text_matches", [])
                    if text_matches:
                        snippet = text_matches[0].get("fragment", "")
                    else:
                        snippet = item.get("name", "")

                    if regex_pattern and not regex_pattern.search(snippet):
                        continue

                    yield SourceResult(
                        provider_name=self.name,
                        target_url=item.get("html_url", ""),
                        code_snippet=snippet,
                        metadata={
                            "repo": item.get("repository", {}).get("full_name", ""),
                            "path": item.get("path", ""),
                            "score": item.get("score", 0),
                        },
                    )

                total = data.get("total_count", 0)
                if page * 30 >= min(total, 1000):
                    return
                page += 1
