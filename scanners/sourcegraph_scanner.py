import json
import logging
from typing import AsyncIterator

import httpx

from scanners.base import BaseScanner
from core.models import SourceResult, SearchMode

logger = logging.getLogger(__name__)


class SourcegraphScanner(BaseScanner):
    name = "sourcegraph"

    def is_configured(self) -> bool:
        return True  # Public search works without a token

    async def scan(self, query: str, mode: SearchMode) -> AsyncIterator[SourceResult]:
        base_url = self.config.get("sourcegraph", {}).get(
            "base_url", "https://sourcegraph.com"
        )
        token = self.config.get("sourcegraph", {}).get("token", "")

        if mode == SearchMode.REGEX:
            sg_query = f"/{query}/ type:file count:100"
        else:
            sg_query = f'content:"{query}" type:file count:100'

        headers = {"Accept": "text/event-stream"}
        if token:
            headers["Authorization"] = f"token {token}"

        params = {"q": sg_query, "v": "V3", "display": "100"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            await self.rate_limiter.acquire(self.name)

            try:
                async with client.stream(
                    "GET",
                    f"{base_url}/.api/search/stream",
                    params=params,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        logger.error(f"[sourcegraph] HTTP {response.status_code}: {body[:200]}")
                        return

                    event_type = ""
                    async for line in response.aiter_lines():
                        if self.is_cancelled:
                            return

                        line = line.strip()
                        if not line:
                            event_type = ""
                            continue

                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                            if event_type == "done":
                                return
                            continue

                        if line.startswith("data:") and event_type == "matches":
                            try:
                                data = json.loads(line[5:])
                            except json.JSONDecodeError:
                                continue

                            matches = data if isinstance(data, list) else [data]
                            for match in matches:
                                if self.is_cancelled:
                                    return
                                if match.get("type") == "content":
                                    repo = match.get("repository", "")
                                    path = match.get("path", "")
                                    language = match.get("language", "")
                                    repo_stars = match.get("repoStars", 0)
                                    for line_match in match.get("lineMatches", []):
                                        snippet = line_match.get("line", "")
                                        line_num = line_match.get("lineNumber", 0)
                                        yield SourceResult(
                                            provider_name=self.name,
                                            target_url=f"{base_url}/{repo}/-/blob/{path}?L{line_num}",
                                            code_snippet=snippet,
                                            metadata={
                                                "repo": repo,
                                                "path": path,
                                                "line_number": line_num,
                                                "language": language,
                                                "repo_stars": repo_stars,
                                            },
                                        )
                                elif match.get("type") == "path":
                                    repo = match.get("repository", "")
                                    path = match.get("path", "")
                                    yield SourceResult(
                                        provider_name=self.name,
                                        target_url=f"{base_url}/{repo}/-/blob/{path}",
                                        code_snippet=path,
                                        metadata={
                                            "repo": repo,
                                            "path": path,
                                        },
                                    )
            except httpx.ReadTimeout:
                logger.warning("[sourcegraph] Stream read timeout")
            except httpx.ConnectError as e:
                logger.error(f"[sourcegraph] Connection error: {e}")
