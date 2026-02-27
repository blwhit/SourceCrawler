import asyncio
import logging
import urllib.parse
from typing import AsyncIterator

from scanners.base import BaseScanner
from core.models import SourceResult, SearchMode

logger = logging.getLogger(__name__)

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


class PlaywrightManager:
    """Lazy singleton for Playwright browser instance."""
    _instance: "PlaywrightManager | None" = None
    _lock: asyncio.Lock | None = None
    _desired_headless: bool = True

    def __init__(self):
        self._playwright = None
        self._browser: "Browser | None" = None
        self._semaphore = asyncio.Semaphore(3)
        self._headless = PlaywrightManager._desired_headless

    @classmethod
    async def get_instance(cls) -> "PlaywrightManager":
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def set_headless(cls, headless: bool) -> None:
        """Set headless mode. Stored as class-level default so new instances pick it up."""
        cls._desired_headless = headless
        if cls._instance:
            cls._instance._headless = headless

    async def get_context(self) -> "BrowserContext":
        await self._semaphore.acquire()
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )
        return context

    async def release_context(self, context: "BrowserContext") -> None:
        try:
            await context.close()
        except Exception:
            pass
        self._semaphore.release()

    @classmethod
    async def shutdown(cls) -> None:
        if cls._instance:
            if cls._instance._browser:
                try:
                    await cls._instance._browser.close()
                except Exception:
                    pass
                cls._instance._browser = None
            if cls._instance._playwright:
                try:
                    await cls._instance._playwright.stop()
                except Exception:
                    pass
            cls._instance = None


class PublicWWWScanner(BaseScanner):
    name = "publicwww"

    def is_configured(self) -> bool:
        return PLAYWRIGHT_AVAILABLE

    def _has_credentials(self) -> bool:
        pw_cfg = self.config.get("publicwww", {})
        return bool(pw_cfg.get("email") and pw_cfg.get("password"))

    async def _login(self, page) -> bool:
        """Log into PublicWWW with credentials from config. Returns True on success."""
        pw_cfg = self.config.get("publicwww", {})
        email = pw_cfg.get("email", "")
        password = pw_cfg.get("password", "")
        if not email or not password:
            return False

        try:
            logger.info("[publicwww] Logging in...")
            await page.goto("https://publicwww.com/profile/login.html",
                            wait_until="networkidle", timeout=30000)
            # Wait for JS challenge
            for _ in range(10):
                body = await page.inner_text("body")
                if "email" in body.lower() and "password" in body.lower():
                    break
                await asyncio.sleep(2)

            await page.fill('input[name="email"]', email)
            await page.fill('input[name="password"]', password)
            await page.click('button:has-text("Log In"), input[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # Check if login succeeded (should redirect away from login page)
            url = page.url
            body = await page.inner_text("body")
            if "login" not in url.lower() or "welcome" in body.lower():
                logger.info("[publicwww] Login successful")
                return True
            else:
                logger.warning("[publicwww] Login may have failed, continuing anyway")
                return False
        except Exception as e:
            logger.warning(f"[publicwww] Login error: {e}")
            return False

    async def scan(self, query: str, mode: SearchMode) -> AsyncIterator[SourceResult]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("[publicwww] Playwright not installed, skipping")
            return

        manager = await PlaywrightManager.get_instance()
        context = await manager.get_context()

        try:
            page = await context.new_page()

            # Login if credentials are configured
            if self._has_credentials():
                await self._login(page)

            encoded_query = urllib.parse.quote(query, safe='')
            search_url = f"https://publicwww.com/websites/{encoded_query}/"
            logger.info(f"[publicwww] Navigating to {search_url}")
            await page.goto(search_url, wait_until="networkidle", timeout=30000)

            # Wait for the JS anti-bot challenge to complete (SHA-256 proof-of-work)
            for attempt in range(15):
                if self.is_cancelled:
                    return
                title = await page.title()
                body_text = await page.inner_text("body")
                if "web pages" in title.lower() or "web pages" in body_text.lower():
                    break
                if "processing request" in body_text.lower() or "please enable" in body_text.lower():
                    await asyncio.sleep(2)
                    continue
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)
            else:
                logger.warning("[publicwww] JS challenge did not resolve after waiting")

            logger.info(f"[publicwww] Page loaded: {await page.title()}")

            page_num = 1
            max_pages = 5
            while not self.is_cancelled and page_num <= max_pages:
                await self.rate_limiter.acquire(self.name)

                results = await page.evaluate("""
                    () => {
                        const items = [];
                        const rows = document.querySelectorAll('tr');
                        for (const row of rows) {
                            const cells = row.querySelectorAll('td');
                            if (cells.length < 2) continue;

                            const rank = cells[0] ? cells[0].textContent.trim() : '';
                            const urlCell = cells[1];
                            if (!urlCell) continue;

                            // Extract the partial domain (after any buttons)
                            let domain = urlCell.textContent.trim()
                                .replace(/Sign up to view/gi, '')
                                .replace(/Upgrade to view/gi, '')
                                .trim();

                            // Get snippet if available (3rd column)
                            const snippet = cells[2] ? cells[2].textContent.trim() : '';

                            // Check for real external links
                            let realUrl = '';
                            for (const a of urlCell.querySelectorAll('a')) {
                                const href = a.href || '';
                                if (href && !href.includes('publicwww.com') &&
                                    !href.includes('/profile/') && !href.includes('/prices')) {
                                    realUrl = href;
                                    break;
                                }
                            }

                            if (domain && domain.length > 2 &&
                                !domain.toLowerCase().includes('search') &&
                                !domain.toLowerCase().includes('url')) {
                                items.push({
                                    rank: rank,
                                    domain: domain,
                                    url: realUrl || domain,
                                    snippet: snippet.substring(0, 500),
                                    is_partial: !realUrl,
                                });
                            }
                        }
                        return items;
                    }
                """)

                logger.info(f"[publicwww] Page {page_num}: found {len(results)} results")

                for item in results:
                    if self.is_cancelled:
                        return
                    domain = item.get("domain", "")
                    url = item.get("url", domain)
                    # If URL is just a domain, make it a proper URL
                    if url and not url.startswith("http"):
                        url = f"https://{url}"

                    yield SourceResult(
                        provider_name=self.name,
                        target_url=url,
                        code_snippet=item.get("snippet", "") or domain,
                        metadata={
                            "page": page_num,
                            "rank": item.get("rank", ""),
                            "domain_hint": domain,
                            "is_partial": item.get("is_partial", True),
                        },
                    )

                # Try next page navigation
                next_page_num = page_num + 1
                next_btn = await page.query_selector(
                    f'a.page-link:has-text("{next_page_num}"), '
                    f'.pagination a:has-text("{next_page_num}"), '
                    'a:has-text("Next"), a:has-text("next"), '
                    'a:has-text("»")'
                )
                if next_btn is None:
                    break

                try:
                    await next_btn.click()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.warning(f"[publicwww] Could not navigate to next page: {e}")
                    break

                page_num += 1

        except Exception as e:
            logger.error(f"[publicwww] Error: {e}")
        finally:
            await manager.release_context(context)
