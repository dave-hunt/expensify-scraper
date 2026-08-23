from __future__ import annotations

import json
from typing import Any

RECEIPT_BASE_URL = "https://www.expensify.com/receipts/"


def _first_non_empty(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value)
    return None


def _receipt_object_url(raw: dict[str, Any]) -> str | None:
    obj = raw.get("receiptObject") or raw.get("receipt")
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except json.JSONDecodeError:
            obj = None
    if isinstance(obj, dict):
        for key in ("url", "source"):
            value = obj.get(key)
            if value and str(value).startswith("http"):
                return str(value)
    return _first_non_empty(raw, "receiptObjectUrl", "receiptObjUrl")


def _is_w_filename_url(url: str) -> bool:
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    return name.startswith("w_")


def resolve_receipt_url_from_row(
    receipt_url: str | None,
    raw: dict[str, Any],
) -> str | None:
    """Resolve a downloadable receipt URL from export or Get API fields."""
    object_url = _receipt_object_url(raw)
    if object_url:
        return object_url

    if receipt_url and not _is_w_filename_url(receipt_url):
        return receipt_url

    direct = _first_non_empty(
        raw,
        "receiptSource",
        "receiptURL",
        "receiptNestedSource",
        "receiptObjSource",
    )
    if direct and not _is_w_filename_url(direct):
        return direct

    if receipt_url:
        return receipt_url

    filename = _first_non_empty(
        raw,
        "receiptFilename",
        "filename",
        "receiptNestedFilename",
        "receiptObjFilename",
    )
    if filename:
        return f"{RECEIPT_BASE_URL}{filename}"

    return None


def resolve_receipt_url_via_legacy_api(
    auth_token: str,
    *,
    transaction_id: str | None,
    receipt_id: str | None,
) -> str | None:
    """Look up receipt filename via legacy Expensify API when export omits URL."""
    if not auth_token or not (transaction_id or receipt_id):
        return None

    try:
        from expensify_scraper.classic_api import fetch_transactions

        transactions = fetch_transactions(auth_token)
    except Exception:
        return None

    for txn in transactions:
        if transaction_id and str(txn.get("transactionID", "")) != str(transaction_id):
            continue
        if (
            receipt_id
            and str(txn.get("receiptID", "")) != str(receipt_id)
            and transaction_id
        ):
            continue
        resolved = resolve_receipt_url_from_row(None, txn)
        if resolved:
            return resolved

    return None
