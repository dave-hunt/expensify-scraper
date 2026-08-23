from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from expensify_scraper.receipt_urls import resolve_receipt_url_from_row
from expensify_scraper.utils import parse_expense_date, utc_now_iso


class DownloadStatus(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    SKIPPED = "skipped"
    FAILED = "failed"
    NO_RECEIPT = "no_receipt"


@dataclass
class ExpenseRecord:
    expense_id: str
    report_id: str
    report_name: str | None
    created: str
    modified_created: str | None
    merchant: str | None
    amount: int
    currency: str
    category: str | None
    receipt_id: str | None
    receipt_url: str | None
    expense_date: str
    raw: dict[str, Any]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ExpenseRecord:
        expense_date = parse_expense_date(row).isoformat()
        expense_id = str(
            row.get("expenseID")
            or row.get("transactionID")
            or row.get("externalID")
            or row.get("receiptID")
            or ""
        )
        record = cls(
            expense_id=expense_id,
            report_id=str(row.get("reportID", "")),
            report_name=row.get("reportName"),
            created=str(row.get("created", "")),
            modified_created=row.get("modifiedCreated") or None,
            merchant=row.get("merchant"),
            amount=int(row.get("amount") or 0),
            currency=str(row.get("currency", "USD")),
            category=row.get("category"),
            receipt_id=str(row["receiptID"]) if row.get("receiptID") else None,
            receipt_url=row.get("receiptURL"),
            expense_date=expense_date,
            raw=row,
        )
        resolved = resolve_receipt_url_from_row(record.receipt_url, record.raw)
        if resolved:
            record.receipt_url = resolved
        return record

    @property
    def has_downloadable_receipt(self) -> bool:
        return bool(
            self.receipt_id
            and resolve_receipt_url_from_row(self.receipt_url, self.raw)
        )


class ManifestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ManifestStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                expense_id TEXT PRIMARY KEY,
                report_id TEXT,
                report_name TEXT,
                created TEXT,
                modified_created TEXT,
                merchant TEXT,
                amount INTEGER,
                currency TEXT,
                category TEXT,
                receipt_id TEXT,
                receipt_url TEXT,
                expense_date TEXT,
                raw_json TEXT,
                imported_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_expenses_receipt_id ON expenses(receipt_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_expense_date ON expenses(expense_date);

            CREATE TABLE IF NOT EXISTS downloads (
                receipt_id TEXT PRIMARY KEY,
                expense_id TEXT,
                status TEXT NOT NULL,
                local_path TEXT,
                sha256 TEXT,
                error TEXT,
                updated_at TEXT
            );
            """
        )
        self._conn.commit()

    def upsert_expenses(self, rows: Iterable[ExpenseRecord]) -> int:
        count = 0
        now = utc_now_iso()
        for row in rows:
            self._conn.execute(
                """
                INSERT INTO expenses (
                    expense_id, report_id, report_name, created, modified_created,
                    merchant, amount, currency, category, receipt_id, receipt_url,
                    expense_date, raw_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(expense_id) DO UPDATE SET
                    report_id=excluded.report_id,
                    report_name=excluded.report_name,
                    created=excluded.created,
                    modified_created=excluded.modified_created,
                    merchant=excluded.merchant,
                    amount=excluded.amount,
                    currency=excluded.currency,
                    category=excluded.category,
                    receipt_id=excluded.receipt_id,
                    receipt_url=excluded.receipt_url,
                    expense_date=excluded.expense_date,
                    raw_json=excluded.raw_json,
                    imported_at=excluded.imported_at
                """,
                (
                    row.expense_id,
                    row.report_id,
                    row.report_name,
                    row.created,
                    row.modified_created,
                    row.merchant,
                    row.amount,
                    row.currency,
                    row.category,
                    row.receipt_id,
                    row.receipt_url,
                    row.expense_date,
                    json.dumps(row.raw),
                    now,
                ),
            )
            count += 1
        self._conn.commit()
        return count

    def iter_downloadable(self) -> list[ExpenseRecord]:
        """Receipts still needing a download.

        A row marked complete whose file is no longer on disk is included, so
        deleting `out/` and re-running `download` restores the receipts instead
        of reporting nothing to do.
        """
        cur = self._conn.execute(
            """
            SELECT e.*, d.status AS download_status, d.local_path AS download_path
            FROM expenses e
            LEFT JOIN downloads d ON d.receipt_id = e.receipt_id
            WHERE e.receipt_id IS NOT NULL AND e.receipt_id != ''
              AND (d.status IS NULL OR d.status != 'skipped')
            ORDER BY e.expense_date
            """
        )
        pending: list[ExpenseRecord] = []
        for row in cur.fetchall():
            data = dict(row)
            if data.pop("download_status", None) == DownloadStatus.COMPLETE.value:
                path = data.get("download_path")
                if path and Path(path).exists():
                    continue
            data.pop("download_path", None)
            pending.append(self._row_to_expense(data))
        return pending

    def probe_candidates(self, limit: int = 5) -> list[ExpenseRecord]:
        """Receipts worth testing a session against.

        Previously-downloaded receipts come first: they are known to be reachable,
        so a failure against them means the session is genuinely bad rather than
        the receipt being inaccessible.
        """
        cur = self._conn.execute(
            """
            SELECT e.* FROM expenses e
            JOIN downloads d ON d.receipt_id = e.receipt_id
            WHERE d.status = 'complete'
            ORDER BY d.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        candidates = [self._row_to_expense(dict(row)) for row in cur.fetchall()]
        seen = {c.receipt_id for c in candidates}
        for record in self.iter_downloadable():
            if len(candidates) >= limit * 2:
                break
            if record.receipt_id not in seen:
                candidates.append(record)
        return candidates

    def get_download(self, receipt_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM downloads WHERE receipt_id = ?", (receipt_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def set_download(
        self,
        receipt_id: str,
        *,
        expense_id: str,
        status: DownloadStatus,
        local_path: str | None = None,
        sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO downloads (
                receipt_id, expense_id, status, local_path, sha256, error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(receipt_id) DO UPDATE SET
                expense_id=excluded.expense_id,
                status=excluded.status,
                local_path=excluded.local_path,
                sha256=excluded.sha256,
                error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (
                receipt_id,
                expense_id,
                status.value,
                local_path,
                sha256,
                error,
                utc_now_iso(),
            ),
        )
        self._conn.commit()

    def counts(self) -> dict[str, int]:
        total = self._conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        with_receipt = self._conn.execute(
            """
            SELECT COUNT(*) FROM expenses
            WHERE receipt_id IS NOT NULL AND receipt_id != ''
              AND receipt_url IS NOT NULL AND receipt_url != ''
            """
        ).fetchone()[0]
        no_receipt = total - with_receipt
        complete = self._conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE status = 'complete'"
        ).fetchone()[0]
        failed = self._conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE status = 'failed'"
        ).fetchone()[0]
        skipped = self._conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE status = 'skipped'"
        ).fetchone()[0]
        pending = with_receipt - complete - failed - skipped
        return {
            "total_expenses": total,
            "with_receipt": with_receipt,
            "no_receipt": no_receipt,
            "complete": complete,
            "failed": failed,
            "skipped": skipped,
            "pending": max(pending, 0),
        }

    def export_csv(self, csv_path: Path) -> None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        cur = self._conn.execute(
            """
            SELECT expense_id, report_id, report_name, expense_date, merchant,
                   amount, currency, category, receipt_id, receipt_url
            FROM expenses ORDER BY expense_date, expense_id
            """
        )
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([column[0] for column in cur.description])
            writer.writerows(cur.fetchall())

    def all_expenses(self) -> list[ExpenseRecord]:
        cur = self._conn.execute("SELECT * FROM expenses ORDER BY expense_date")
        return [self._row_to_expense(dict(row)) for row in cur.fetchall()]

    def failed_downloads(self) -> list[dict[str, Any]]:
        return self._downloads_with_status(DownloadStatus.FAILED)

    def skipped_downloads(self) -> list[dict[str, Any]]:
        """Receipts Expensify refused to serve.

        These differ from expenses that never had a file: they carry a receipt
        URL, so they look downloadable, but the server rejects the request and
        re-running `download` will not retrieve them.
        """
        return self._downloads_with_status(DownloadStatus.SKIPPED)

    def _downloads_with_status(self, status: DownloadStatus) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT d.*, e.merchant, e.expense_date, e.receipt_url
            FROM downloads d
            JOIN expenses e ON e.expense_id = d.expense_id
            WHERE d.status = ?
            ORDER BY e.expense_date
            """,
            (status.value,),
        )
        return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_expense(row: dict[str, Any]) -> ExpenseRecord:
        raw = json.loads(row["raw_json"]) if row.get("raw_json") else {}
        return ExpenseRecord(
            expense_id=row["expense_id"],
            report_id=row["report_id"] or "",
            report_name=row.get("report_name"),
            created=row.get("created") or "",
            modified_created=row.get("modified_created"),
            merchant=row.get("merchant"),
            amount=int(row.get("amount") or 0),
            currency=row.get("currency") or "USD",
            category=row.get("category"),
            receipt_id=row.get("receipt_id"),
            receipt_url=row.get("receipt_url"),
            expense_date=row.get("expense_date") or "",
            raw=raw,
        )
