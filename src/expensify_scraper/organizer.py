from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from slugify import slugify

from expensify_scraper.manifest import ExpenseRecord
from expensify_scraper.utils import format_amount_cents

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def merchant_slug(merchant: str | None, max_length: int = 40) -> str:
    base = slugify(merchant or "unknown", max_length=max_length, word_boundary=True)
    return base or "unknown"


def guess_extension(content_type: str | None, url: str) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in CONTENT_TYPE_EXTENSIONS:
            return CONTENT_TYPE_EXTENSIONS[ct]

    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".heic", ".heif"):
        if path.endswith(ext):
            return ext if ext != ".jpeg" else ".jpg"

    return ".bin"


def build_receipt_path(
    output_dir: Path,
    expense: ExpenseRecord,
    extension: str,
) -> Path:
    expense_date = re.sub(r"[^0-9-]", "", expense.expense_date or "") or "0000-00-00"
    year, month, _day = (expense_date.split("-") + ["01", "01"])[:3]
    folder = output_dir / (year or "0000") / (month or "00").zfill(2)
    folder.mkdir(parents=True, exist_ok=True)

    amount = format_amount_cents(expense.amount)
    merchant = merchant_slug(expense.merchant)
    receipt_id = expense.receipt_id or expense.expense_id
    safe_receipt = re.sub(r"[^A-Za-z0-9_-]", "", receipt_id)[:32]
    safe_currency = re.sub(r"[^A-Za-z0-9]", "", expense.currency)[:8]

    filename = f"{expense_date}_{merchant}_{amount}{safe_currency}_{safe_receipt}{extension}"
    return folder / filename


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
