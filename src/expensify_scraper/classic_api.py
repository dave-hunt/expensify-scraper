from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from expensify_scraper.manifest import ExpenseRecord, ManifestStore

# Public client identifiers shipped in Expensify's open-source app
# (Expensify/App, src/CONFIG.ts). Not a user credential.
CHAT_PARTNER_NAME = "chat-expensify-com"
CHAT_PARTNER_PASSWORD = "e21965746fd75f82bb66"
DEFAULT_APP_VERSION = "9.4.59-0"
AUTHENTICATE_URL = "https://www.expensify.com/api/Authenticate?"
GET_URL = "https://www.expensify.com/api/Get?"
GITHUB_APP_PACKAGE = "https://raw.githubusercontent.com/Expensify/App/main/package.json"

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://new.expensify.com/",
    "Accept": "*/*",
}


class ClassicAPIError(Exception):
    pass


@dataclass
class ClassicTokens:
    auth_token: str
    encrypted_auth_token: str = ""


def resolve_app_version(current: str = DEFAULT_APP_VERSION) -> str:
    try:
        response = httpx.get(GITHUB_APP_PACKAGE, timeout=10.0)
        version = response.json().get("version")
        if isinstance(version, str) and version:
            return version
    except Exception:
        pass
    return current


def _authenticate(login: str, password: str, app_version: str) -> dict[str, Any]:
    response = httpx.post(
        AUTHENTICATE_URL,
        data={
            "useExpensifyLogin": "false",
            "partnerName": CHAT_PARTNER_NAME,
            "partnerPassword": CHAT_PARTNER_PASSWORD,
            "partnerUserID": login,
            "partnerUserSecret": password,
            "appversion": app_version,
            "referer": "ecash",
            "platform": "web",
            "api_setCookie": "false",
        },
        headers=API_HEADERS,
        timeout=30.0,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise ClassicAPIError("Expensify Authenticate returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise ClassicAPIError("Expensify Authenticate returned an unexpected payload")
    return payload


def mint_classic_tokens(login: str, password: str) -> ClassicTokens:
    """Mint API tokens the same way the official New Expensify app does."""
    if not login or not password:
        raise ClassicAPIError(
            "Saved login is missing generated credentials. Run: expensify-scraper auth"
        )
    version = DEFAULT_APP_VERSION
    payload = _authenticate(login, password, version)
    if payload.get("jsonCode") == 426:
        version = resolve_app_version(version)
        payload = _authenticate(login, password, version)
    if payload.get("jsonCode") != 200 or not payload.get("authToken"):
        code = payload.get("jsonCode")
        message = str(payload.get("message") or "unknown error")
        raise ClassicAPIError(f"Expensify Authenticate failed ({code}): {message}")
    return ClassicTokens(
        auth_token=str(payload["authToken"]),
        encrypted_auth_token=str(payload.get("encryptedAuthToken") or ""),
    )


def fetch_transactions(auth_token: str) -> list[dict[str, Any]]:
    if not auth_token:
        raise ClassicAPIError("No API token available. Run: expensify-scraper auth")
    response = httpx.post(
        GET_URL,
        data={
            "authToken": auth_token,
            "returnValueList": "transactionList",
            "referer": "ecash",
            "platform": "web",
            "appversion": DEFAULT_APP_VERSION,
            "api_setCookie": "false",
        },
        headers=API_HEADERS,
        timeout=60.0,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise ClassicAPIError("Expensify Get returned a non-JSON response") from exc
    if not isinstance(payload, dict) or payload.get("jsonCode") != 200:
        code = payload.get("jsonCode") if isinstance(payload, dict) else None
        raise ClassicAPIError(f"Expensify Get failed ({code})")
    rows = payload.get("transactionList") or []
    return [row for row in rows if isinstance(row, dict)]


def expense_row_from_transaction(txn: dict[str, Any]) -> dict[str, Any]:
    obj = txn.get("receiptObject") if isinstance(txn.get("receiptObject"), dict) else {}
    receipt_id = txn.get("receiptID") or obj.get("receiptID")
    return {
        "transactionID": txn.get("transactionID"),
        "reportID": txn.get("reportID"),
        "reportName": txn.get("reportName") or "",
        "created": txn.get("created") or txn.get("inserted") or "",
        "modifiedCreated": txn.get("modifiedCreated") or "",
        "merchant": txn.get("merchant"),
        "amount": txn.get("amount") or 0,
        "currency": txn.get("currency") or "USD",
        "category": txn.get("category"),
        "receiptID": receipt_id,
        "receiptURL": obj.get("url"),
        "receiptFilename": txn.get("receiptFilename"),
        "receiptObject": obj,
        "receiptState": txn.get("receiptState") or obj.get("state"),
    }


def enrich_manifest_from_api(store: ManifestStore, auth_token: str) -> int:
    records: list[ExpenseRecord] = []
    for txn in fetch_transactions(auth_token):
        record = ExpenseRecord.from_row(expense_row_from_transaction(txn))
        if record.has_downloadable_receipt:
            records.append(record)
    if not records:
        return 0
    return store.upsert_expenses(records)
