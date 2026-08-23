from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from rich.progress import Progress, TaskID
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from expensify_scraper.auth import (
    DEFAULT_RECEIPT_HEADERS,
    AuthStrategy,
    PlaywrightReceiptResponse,
    load_browser_cookies,
    load_session,
    probe_receipt_auth,
    response_looks_like_receipt,
    save_session,
)
from expensify_scraper.browser_downloads import ReceiptBrowser
from expensify_scraper.config import Settings
from expensify_scraper.manifest import DownloadStatus, ExpenseRecord, ManifestStore
from expensify_scraper.organizer import build_receipt_path, guess_extension, sha256_bytes
from expensify_scraper.receipt_urls import (
    resolve_receipt_url_from_row,
    resolve_receipt_url_via_legacy_api,
)


class DownloadError(Exception):
    pass


@dataclass
class DownloadOutcome:
    receipt_id: str
    status: DownloadStatus
    local_path: str | None = None
    sha256: str | None = None
    error: str | None = None


class ReceiptDownloader:
    def __init__(self, settings: Settings, store: ManifestStore) -> None:
        self.settings = settings
        self.store = store
        self.session = load_session(settings)
        self._strategy = (
            AuthStrategy(self.session.strategy)
            if self.session.strategy
            else None
        )
        self._playwright_lock = asyncio.Lock()

    def probe_auth_strategy(self, sample_url: str | list[str]) -> AuthStrategy:
        if self._strategy:
            return self._strategy

        strategy = probe_receipt_auth(self.settings, self.session, sample_url)
        if strategy is None:
            raise DownloadError(
                "Could not authenticate receipt download. "
                "Run: expensify-scraper auth"
            )
        self._strategy = strategy
        self.session.strategy = strategy
        save_session(self.settings, self.session)
        return strategy

    @staticmethod
    def _looks_like_receipt(response: httpx.Response | PlaywrightReceiptResponse) -> bool:
        return response_looks_like_receipt(response)

    def _build_request(
        self, url: str, strategy: AuthStrategy
    ) -> tuple[str, dict[str, str], httpx.Cookies]:
        headers = dict(DEFAULT_RECEIPT_HEADERS)
        cookies = httpx.Cookies()
        final_url = url

        if strategy in (AuthStrategy.BROWSER_COOKIES, AuthStrategy.COOKIES_AND_HEADER):
            cookies = load_browser_cookies(self.settings)
        if strategy == AuthStrategy.COOKIE:
            cookies = httpx.Cookies({"authToken": self.session.auth_token})
        if strategy == AuthStrategy.COOKIES_AND_HEADER or strategy == AuthStrategy.HEADER:
            headers["X-Chat-Attachment-Token"] = self.session.auth_token
        elif strategy == AuthStrategy.QUERY:
            sep = "&" if "?" in url else "?"
            final_url = f"{url}{sep}authToken={self.session.auth_token}"

        return final_url, headers, cookies

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        url: str,
        strategy: AuthStrategy,
    ) -> httpx.Response:
        final_url, headers, cookies = self._build_request(url, strategy)
        return await client.get(final_url, headers=headers, cookies=cookies)

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    async def _download_one(
        self,
        client: httpx.AsyncClient,
        expense: ExpenseRecord,
        strategy: AuthStrategy,
    ) -> DownloadOutcome:
        assert expense.receipt_id
        receipt_url = resolve_receipt_url_from_row(expense.receipt_url, expense.raw)
        if not receipt_url:
            receipt_url = resolve_receipt_url_via_legacy_api(
                self.session.auth_token,
                transaction_id=expense.raw.get("transactionID"),
                receipt_id=expense.receipt_id,
            )
        if not receipt_url:
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error="No resolvable receipt URL",
            )

        existing = self.store.get_download(expense.receipt_id)
        if existing and existing.get("status") == DownloadStatus.COMPLETE.value:
            path = existing.get("local_path")
            if path and Path(path).exists():
                return DownloadOutcome(
                    receipt_id=expense.receipt_id,
                    status=DownloadStatus.COMPLETE,
                    local_path=path,
                    sha256=existing.get("sha256"),
                )

        response = await self._fetch(client, receipt_url, strategy)
        if response.status_code == 401:
            raise DownloadError("Auth token rejected while downloading receipts")
        if response.status_code == 403:
            # The strategy was proven against a reachable receipt before this run,
            # so a 403 here means this receipt is not accessible to the account
            # (deleted, or owned by a workspace it has left). Recording it as
            # failed would leave it in the pending set and poison later probes.
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error="Not accessible to this account (403)",
            )
        if response.status_code == 404:
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error="Receipt not found (404)",
            )
        response.raise_for_status()

        if not self._looks_like_receipt(response):
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error=f"Unexpected content-type: {response.headers.get('content-type')}",
            )

        extension = guess_extension(
            response.headers.get("content-type"), receipt_url
        )
        target = build_receipt_path(
            self.settings.expensify_output_dir, expense, extension
        )
        target.write_bytes(response.content)
        digest = sha256_bytes(response.content)

        return DownloadOutcome(
            receipt_id=expense.receipt_id,
            status=DownloadStatus.COMPLETE,
            local_path=str(target),
            sha256=digest,
        )

    async def download_all(
        self,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:

        expenses = self.store.iter_downloadable()
        if not expenses:
            return {"complete": 0, "failed": 0, "skipped": 0}

        strategy = self.probe_auth_strategy(
            [
                url
                for record in self.store.probe_candidates()
                if (url := resolve_receipt_url_from_row(record.receipt_url, record.raw))
            ]
        )
        if strategy == AuthStrategy.PLAYWRIGHT:
            return self._download_all_browser(expenses, progress_callback)
        semaphore = asyncio.Semaphore(self.settings.expensify_download_concurrency)
        stats: dict[str, int] = {"complete": 0, "failed": 0, "skipped": 0}
        total = len(expenses)

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:

            async def worker(expense: ExpenseRecord) -> None:
                async with semaphore:
                    try:
                        outcome = await self._download_one(client, expense, strategy)
                    except Exception as exc:
                        outcome = DownloadOutcome(
                            receipt_id=expense.receipt_id or expense.expense_id,
                            status=DownloadStatus.FAILED,
                            error=str(exc),
                        )

                    self.store.set_download(
                        outcome.receipt_id,
                        expense_id=expense.expense_id,
                        status=outcome.status,
                        local_path=outcome.local_path,
                        sha256=outcome.sha256,
                        error=outcome.error,
                    )
                    if outcome.status == DownloadStatus.COMPLETE:
                        stats["complete"] += 1
                    elif outcome.status == DownloadStatus.SKIPPED:
                        stats["skipped"] += 1
                    else:
                        stats["failed"] += 1
                    if progress_callback:
                        done = stats["complete"] + stats["failed"] + stats["skipped"]
                        progress_callback(done, total)

            await asyncio.gather(*(worker(expense) for expense in expenses))

        return stats

    def _download_all_browser(
        self,
        expenses: list[ExpenseRecord],
        progress_callback: Callable[[int, int], None] | None,
    ) -> dict[str, int]:
        stats: dict[str, int] = {"complete": 0, "failed": 0, "skipped": 0}
        total = len(expenses)
        with ReceiptBrowser(self.settings, self.session) as browser:
            save_session(self.settings, self.session)
            for index, expense in enumerate(expenses, start=1):
                try:
                    outcome = self._download_one_browser(browser, expense)
                except Exception as exc:
                    outcome = DownloadOutcome(
                        receipt_id=expense.receipt_id or expense.expense_id,
                        status=DownloadStatus.FAILED,
                        error=str(exc),
                    )
                self.store.set_download(
                    outcome.receipt_id,
                    expense_id=expense.expense_id,
                    status=outcome.status,
                    local_path=outcome.local_path,
                    sha256=outcome.sha256,
                    error=outcome.error,
                )
                if outcome.status == DownloadStatus.COMPLETE:
                    stats["complete"] += 1
                elif outcome.status == DownloadStatus.SKIPPED:
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                if progress_callback:
                    progress_callback(index, total)
        return stats

    def _download_one_browser(
        self,
        browser: ReceiptBrowser,
        expense: ExpenseRecord,
    ) -> DownloadOutcome:
        assert expense.receipt_id
        receipt_url = resolve_receipt_url_from_row(expense.receipt_url, expense.raw)
        if not receipt_url:
            receipt_url = resolve_receipt_url_via_legacy_api(
                self.session.auth_token,
                transaction_id=expense.raw.get("transactionID"),
                receipt_id=expense.receipt_id,
            )
        if not receipt_url:
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error="No resolvable receipt URL",
            )

        existing = self.store.get_download(expense.receipt_id)
        if existing and existing.get("status") == DownloadStatus.COMPLETE.value:
            path = existing.get("local_path")
            if path and Path(path).exists():
                return DownloadOutcome(
                    receipt_id=expense.receipt_id,
                    status=DownloadStatus.COMPLETE,
                    local_path=path,
                    sha256=existing.get("sha256"),
                )

        response = browser.fetch(receipt_url)
        if response.status_code == 401:
            raise DownloadError("Auth rejected while downloading receipts")
        if response.status_code == 403:
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error="Not accessible to this account (403)",
            )
        if response.status_code == 404:
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error="Receipt not found (404)",
            )
        if response.status_code >= 400:
            raise DownloadError(f"HTTP {response.status_code} downloading receipt")
        if not self._looks_like_receipt(response):
            return DownloadOutcome(
                receipt_id=expense.receipt_id,
                status=DownloadStatus.SKIPPED,
                error=f"Unexpected content-type: {response.headers.get('content-type')}",
            )

        extension = guess_extension(
            response.headers.get("content-type"), receipt_url
        )
        target = build_receipt_path(
            self.settings.expensify_output_dir, expense, extension
        )
        target.write_bytes(response.content)
        return DownloadOutcome(
            receipt_id=expense.receipt_id,
            status=DownloadStatus.COMPLETE,
            local_path=str(target),
            sha256=sha256_bytes(response.content),
        )


def run_download(settings: Settings, store: ManifestStore) -> dict[str, int]:
    downloader = ReceiptDownloader(settings, store)
    with Progress() as progress:
        task: TaskID = progress.add_task("Downloading receipts...", total=1)

        def on_progress(done: int, total: int) -> None:
            progress.update(task, total=total, completed=done)

        return asyncio.run(downloader.download_all(progress_callback=on_progress))
