from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from expensify_scraper.config import Settings
from expensify_scraper.rate_limiter import RateLimiter
from expensify_scraper.utils import DateWindow, iter_year_windows


class IntegrationServerError(Exception):
    pass


class RateLimitError(IntegrationServerError):
    pass


ALL_REPORT_STATES = "OPEN,SUBMITTED,APPROVED,REIMBURSED,ARCHIVED"


@dataclass
class ExportResult:
    filename: str
    content: str
    window: DateWindow | None = None


class IntegrationServerClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._limiter = RateLimiter()
        self._client = httpx.Client(timeout=120.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> IntegrationServerClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def credentials(self) -> dict[str, str]:
        user_id, secret = self.settings.require_integration_credentials()
        return {
            "partnerUserID": user_id,
            "partnerUserSecret": secret,
        }

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, RateLimitError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _post(self, data: dict[str, str]) -> httpx.Response:
        self._limiter.acquire()
        response = self._client.post(self.settings.integration_server_url, data=data)
        if response.status_code == 429:
            raise RateLimitError("Rate limited by Expensify Integration Server")
        response.raise_for_status()
        return response

    def _request_job(self, job: dict[str, Any], template: str | None = None) -> str:
        payload = {"requestJobDescription": json.dumps(job)}
        if template is not None:
            payload["template"] = template

        response = self._post(payload)
        text = response.text.strip()

        if text.startswith("{"):
            parsed = json.loads(text)
            code = parsed.get("responseCode")
            if code and int(code) != 200:
                raise IntegrationServerError(
                    f"Integration Server error {code}: {parsed.get('responseMessage', text)}"
                )
            if "filename" in parsed:
                return str(parsed["filename"])
            if "fileName" in parsed:
                return str(parsed["fileName"])

        # Immediate response mode returns bare filename
        if text and not text.startswith("<"):
            return text.splitlines()[0].strip()

        raise IntegrationServerError(f"Unexpected Integration Server response: {text[:500]}")

    def export_combined_report(
        self,
        template: str,
        *,
        start_date: str,
        end_date: str,
        limit: str | None = None,
        report_state: str = ALL_REPORT_STATES,
    ) -> ExportResult:
        filters: dict[str, str] = {
            "startDate": start_date,
            "endDate": end_date,
        }
        input_settings: dict[str, Any] = {
            "type": "combinedReportData",
            "reportState": report_state,
            "filters": filters,
        }
        if limit is not None:
            input_settings["limit"] = limit

        job = {
            "type": "file",
            "credentials": self.credentials,
            "onReceive": {"immediateResponse": ["returnRandomFileName"]},
            "inputSettings": input_settings,
            "outputSettings": {"fileExtension": "csv"},
        }
        filename = self._request_job(job, template=template)
        content = self.download_file(filename)
        return ExportResult(filename=filename, content=content)

    def download_file(self, filename: str) -> str:
        job = {
            "type": "download",
            "credentials": self.credentials,
            "fileName": filename,
            "fileSystem": "integrationServer",
        }
        response = self._post({"requestJobDescription": json.dumps(job)})
        return response.text

    def export_all_windows(
        self,
        template: str,
        since: date | None = None,
        until: date | None = None,
    ) -> list[ExportResult]:
        since_date = since or self.settings.expensify_since
        until_date = until or date.today()
        results: list[ExportResult] = []
        for window in iter_year_windows(since_date, until_date):
            result = self.export_combined_report(
                template,
                start_date=window.start.isoformat(),
                end_date=window.end.isoformat(),
            )
            result.window = window
            results.append(result)
        return results
