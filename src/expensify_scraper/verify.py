from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from expensify_scraper.config import Settings
from expensify_scraper.manifest import DownloadStatus, ManifestStore
from expensify_scraper.utils import utc_now_iso


def verify_downloads(settings: Settings, store: ManifestStore) -> dict[str, Any]:
    counts = store.counts()
    expenses = store.all_expenses()

    missing_files: list[dict[str, str]] = []
    failed = store.failed_downloads()
    skipped = store.skipped_downloads()

    for expense in expenses:
        if not expense.has_downloadable_receipt:
            continue
        assert expense.receipt_id
        record = store.get_download(expense.receipt_id)
        if not record or record.get("status") != DownloadStatus.COMPLETE.value:
            continue
        path = record.get("local_path")
        if not path or not Path(path).exists():
            missing_files.append(
                {
                    "receipt_id": expense.receipt_id,
                    "expense_id": expense.expense_id,
                    "expected_path": path or "",
                }
            )

    on_disk = list(settings.expensify_output_dir.rglob("*"))
    file_count = sum(1 for p in on_disk if p.is_file())

    summary: dict[str, Any] = {
        "verified_at": utc_now_iso(),
        "counts": counts,
        "files_on_disk": file_count,
        "failed_downloads": failed,
        "skipped_downloads": skipped,
        "missing_files": missing_files,
        "complete": counts["failed"] == 0 and counts["pending"] == 0 and not missing_files,
        "notes": [
            "no_receipt counts expenses with no file to fetch at all "
            "(eReceipts, map receipts, bank wire fees).",
            "skipped_downloads are receipts that do have a URL but which Expensify "
            "refused to serve. They are absent from out/ and re-running download "
            "will not retrieve them.",
            "complete reflects work still worth retrying, so it stays true when the "
            "only gaps are skipped_downloads.",
            "combinedReportData is report-scoped; unreported expenses may be absent "
            "from the manifest.",
        ],
    }

    summary_path = settings.expensify_output_dir / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
