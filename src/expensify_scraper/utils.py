from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date


def iter_year_windows(since: date, until: date | None = None) -> list[DateWindow]:
    """Split a date range into windows of at most 365 days (Expensify API limit)."""
    if until is None:
        until = date.today()
    if since > until:
        return []

    windows: list[DateWindow] = []
    cursor = since
    while cursor <= until:
        # Inclusive end; max span is 365 days
        end = min(cursor + timedelta(days=364), until)
        windows.append(DateWindow(start=cursor, end=end))
        cursor = end + timedelta(days=1)
    return windows


def parse_expense_date(expense: dict[str, Any]) -> date:
    raw = expense.get("modifiedCreated") or expense.get("created") or ""
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        # A single unparseable date must not abort a whole sync.
        return date.today()


def parse_ndjson_lines(content: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def format_amount_cents(amount_cents: int | str) -> str:
    cents = int(amount_cents)
    dollars = abs(cents) / 100
    sign = "-" if cents < 0 else ""
    if dollars == int(dollars):
        return f"{sign}{int(dollars)}"
    return f"{sign}{dollars:.2f}"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
