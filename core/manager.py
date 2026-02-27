import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable

import yaml

from core.models import ScanRequest, SourceResult, SearchMode, ScanStatus
from core.rate_limiter import RateLimitRegistry
from scanners import ALL_SCANNERS
from scanners.base import BaseScanner

logger = logging.getLogger(__name__)


class ScannerManager:
    """Orchestrates all scanners for a search query."""

    def __init__(self, config_path: str = "config.yaml"):
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file, "r") as f:
                self.config = yaml.safe_load(f) or {}
        else:
            self.config = {}

        self.rate_limiter = RateLimitRegistry()
        self._active_scans: dict[str, ScanRequest] = {}
        self._scanner_tasks: dict[str, list[asyncio.Task]] = {}
        self._scanners_cache: dict[str, list[BaseScanner]] = {}

        limits = self.config.get("rate_limits", {})
        for provider, rpm in limits.items():
            self.rate_limiter.register(provider, rpm)

    def _create_scanners(self) -> list[BaseScanner]:
        """Instantiate all configured scanners, respecting disabled list."""
        disabled = self.config.get("_disabled_scanners", {})
        scanners = []
        for scanner_cls in ALL_SCANNERS:
            scanner = scanner_cls(
                rate_limiter=self.rate_limiter,
                config=self.config,
            )
            if disabled.get(scanner.name):
                logger.info(f"Scanner skipped (disabled by user): {scanner.name}")
                continue
            if scanner.is_configured():
                scanners.append(scanner)
                logger.info(f"Scanner enabled: {scanner.name}")
            else:
                logger.info(f"Scanner skipped (not configured): {scanner.name}")
        return scanners

    async def run_scan(
        self,
        scan_request: ScanRequest,
        on_result: Callable[[SourceResult], Awaitable[None]],
        on_status: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """Run all scanners in parallel, streaming results via callbacks."""
        scan_request.status = ScanStatus.RUNNING
        self._active_scans[scan_request.scan_id] = scan_request
        scanners = self._create_scanners()
        self._scanners_cache[scan_request.scan_id] = scanners

        if not scanners:
            await on_status("system", "No scanners configured. Add API keys to config.yaml.")
            scan_request.status = ScanStatus.COMPLETED
            return

        async def run_single_scanner(scanner: BaseScanner):
            try:
                await on_status(scanner.name, "started")
                count = 0
                async for result in scanner.scan(scan_request.query, scan_request.mode):
                    scan_request.results.append(result)
                    await on_result(result)
                    count += 1
                await on_status(scanner.name, f"completed ({count} results)")
            except asyncio.CancelledError:
                await on_status(scanner.name, "cancelled")
            except Exception as e:
                error_msg = f"{scanner.name}: {str(e)}"
                scan_request.errors.append(error_msg)
                logger.exception(f"Scanner error: {error_msg}")
                await on_status(scanner.name, f"error: {str(e)[:100]}")

        tasks = [
            asyncio.create_task(run_single_scanner(s), name=f"scanner-{s.name}")
            for s in scanners
        ]
        self._scanner_tasks[scan_request.scan_id] = tasks

        await asyncio.gather(*tasks, return_exceptions=True)

        if scan_request.status == ScanStatus.RUNNING:
            scan_request.status = ScanStatus.COMPLETED

        self._scanner_tasks.pop(scan_request.scan_id, None)
        self._scanners_cache.pop(scan_request.scan_id, None)

    async def cancel_scan(self, scan_id: str) -> bool:
        """Cancel a running scan."""
        scan = self._active_scans.get(scan_id)
        if not scan:
            return False
        scan.status = ScanStatus.CANCELLED
        for scanner in self._scanners_cache.get(scan_id, []):
            scanner.cancel()
        for task in self._scanner_tasks.get(scan_id, []):
            task.cancel()
        return True

    def get_scan(self, scan_id: str) -> ScanRequest | None:
        return self._active_scans.get(scan_id)
