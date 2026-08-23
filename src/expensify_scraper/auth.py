from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from playwright.sync_api import BrowserContext, Page, sync_playwright

from expensify_scraper.config import Settings


class AuthError(Exception):
    pass


class AuthStrategy(str, Enum):
    PLAYWRIGHT = "playwright"
    BROWSER_COOKIES = "browser_cookies"
    COOKIE = "cookie"
    HEADER = "header"
    QUERY = "query"
    COOKIES_AND_HEADER = "cookies_and_header"
    CLASSIC_TOKEN = "classic_token"


SESSION_COOKIE_NAMES = frozenset(
    {"cfidsgib-w-expensify", "cf_clearance", "agentroutestate", "__cf_bm"}
)
SIGNIN_URL_HINTS = ("signin", "sign-in", "login", "auth/validate")

DEFAULT_RECEIPT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://new.expensify.com/",
    "Accept": "*/*",
}


@dataclass
class AuthSession:
    auth_token: str
    encrypted_auth_token: str = ""
    strategy: AuthStrategy | None = None
    captured_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "auth_token": self.auth_token,
            "encrypted_auth_token": self.encrypted_auth_token,
            "strategy": self.strategy.value if self.strategy else None,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthSession:
        strategy = data.get("strategy")
        return cls(
            auth_token=str(data.get("auth_token") or ""),
            encrypted_auth_token=str(data.get("encrypted_auth_token") or ""),
            strategy=AuthStrategy(strategy) if strategy else None,
            captured_at=data.get("captured_at"),
        )


ONYX_DB_READ_JS = """
async () => {
  const openDb = (name) => new Promise((resolve, reject) => {
    const req = indexedDB.open(name);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
  });

  const readAll = (db) => new Promise((resolve, reject) => {
    const tx = db.transaction(db.objectStoreNames, 'readonly');
    const stores = {};
    let pending = db.objectStoreNames.length;
    if (!pending) return resolve(stores);
    for (const name of db.objectStoreNames) {
      const store = tx.objectStore(name);
      const req = store.getAll();
      req.onsuccess = () => {
        stores[name] = req.result;
        pending -= 1;
        if (pending === 0) resolve(stores);
      };
      req.onerror = () => reject(req.error);
    }
  });

  const names = await new Promise((resolve) => {
    const req = indexedDB.databases ? indexedDB.databases() : Promise.resolve([]);
    req.then ? req.then(resolve).catch(() => resolve([])) : resolve([]);
  });

  const dbNames = (names || []).map((d) => d.name).filter(Boolean);
  const fallback = ['Expensify', 'Onyx', 'expensify', 'onyx'];
  const candidates = [...new Set([...dbNames, ...fallback])];

  for (const dbName of candidates) {
    try {
      const db = await openDb(dbName);
      const stores = await readAll(db);
      db.close();
      for (const rows of Object.values(stores)) {
        for (const row of rows) {
          const blob = JSON.stringify(row);
          const tokenMatch = blob.match(/"authToken"\\s*:\\s*"([A-Fa-f0-9]{32,})"/);
          if (tokenMatch) return tokenMatch[1];
        }
      }
    } catch (e) {
      // try next db
    }
  }
  return null;
}
"""


def _extract_token_from_request(post_data: str | None) -> str | None:
    if not post_data:
        return None
    match = re.search(r'"authToken"\s*:\s*"([A-Fa-f0-9]{32,})"', post_data)
    if match:
        return match.group(1)
    match = re.search(r"authToken=([A-Fa-f0-9]{32,})", post_data)
    if match:
        return match.group(1)
    return None


def _safe_request_text(request: Any) -> str | None:
    """Read request body as text; ignore binary/gzip payloads."""
    try:
        data = request.post_data
        if isinstance(data, str):
            return data
    except Exception:
        pass
    try:
        raw = request.post_data_buffer
        if not raw:
            return None
        if raw[:2] == b"\x1f\x8b":
            return None
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None


def _token_from_cookies(context: BrowserContext, domain: str) -> str | None:
    for cookie in context.cookies():
        if cookie.get("name") == "authToken" and domain in cookie.get("domain", ""):
            return cookie.get("value")
    return None


def _token_from_indexeddb(page: Page) -> str | None:
    try:
        token = page.evaluate(ONYX_DB_READ_JS)
        if token:
            return str(token)
    except Exception:
        return None
    return None


def _looks_like_signin(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in SIGNIN_URL_HINTS)


def login_and_capture(settings: Settings, *, timeout_seconds: int = 300) -> AuthSession:
    settings.ensure_dirs()
    captured: dict[str, str | None] = {"token": None, "encrypted": None}

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=False)
        except Exception as exc:
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message.lower():
                raise AuthError(
                    "Playwright Chromium is not installed for this virtualenv.\n"
                    "Run:  .venv/bin/playwright install chromium\n"
                    "Then retry:  expensify-scraper auth"
                ) from exc
            raise AuthError(f"Failed to launch browser: {message}") from exc
        context = browser.new_context()
        page = context.new_page()

        def on_request(request: Any) -> None:
            if captured["token"]:
                return
            token = _extract_token_from_request(_safe_request_text(request))
            if token:
                captured["token"] = token

        page.on("request", on_request)
        page.goto(settings.new_expensify_url, wait_until="domcontentloaded")

        print(
            "Complete login in the browser window.\n"
            "When you can see your inbox (not the sign-in page),\n"
            "return here and press Enter."
        )
        try:
            input()
        except EOFError:
            page.wait_for_timeout(timeout_seconds * 1000)

        from expensify_scraper.browser_downloads import (
            extract_onyx_credentials,
            extract_onyx_session,
        )
        from expensify_scraper.classic_api import ClassicAPIError, mint_classic_tokens

        onyx_auth, onyx_encrypted = extract_onyx_session(page)
        captured["token"] = (
            captured["token"]
            or onyx_auth
            or _token_from_cookies(context, settings.expensify_domain)
            or _token_from_indexeddb(page)
        )
        captured["encrypted"] = onyx_encrypted
        login, password = extract_onyx_credentials(page)
        try:
            minted = mint_classic_tokens(login, password)
            captured["token"] = minted.auth_token
            captured["encrypted"] = minted.encrypted_auth_token or captured["encrypted"]
        except ClassicAPIError:
            pass
        if _looks_like_signin(page.url) and not captured["token"]:
            browser.close()
            raise AuthError(
                "Still on the sign-in page. Finish login, then run auth again "
                "and press Enter after the inbox loads."
            )

        try:
            context.storage_state(
                path=str(settings.storage_state_path),
                indexed_db=True,
            )
        except TypeError:
            context.storage_state(path=str(settings.storage_state_path))
        browser.close()

    if not captured["token"] and not has_browser_session(settings):
        raise AuthError(
            "Login finished but no Expensify session was saved. Retry auth."
        )

    from expensify_scraper.utils import utc_now_iso

    session = AuthSession(
        auth_token=captured["token"] or "",
        encrypted_auth_token=captured.get("encrypted") or "",
        captured_at=utc_now_iso(),
    )
    settings.token_path.write_text(
        json.dumps(session.to_dict(), indent=2),
        encoding="utf-8",
    )
    return session


def fetch_receipt_with_playwright(
    settings: Settings,
    url: str,
    auth_token: str = "",
) -> tuple[int, dict[str, str], bytes]:
    """Download a receipt using the saved Playwright browser session."""
    if not settings.storage_state_path.exists():
        raise AuthError("No saved browser session. Run: expensify-scraper auth")

    headers = dict(DEFAULT_RECEIPT_HEADERS)
    if auth_token:
        headers["X-Chat-Attachment-Token"] = auth_token

    with sync_playwright() as playwright:
        request_context = playwright.request.new_context(
            storage_state=str(settings.storage_state_path),
            extra_http_headers=headers,
        )
        try:
            response = request_context.get(url, timeout=120_000)
            body = response.body()
            headers_out = {key: value for key, value in response.headers.items()}
            return response.status, headers_out, body
        finally:
            request_context.dispose()


class PlaywrightReceiptResponse:
    def __init__(self, status_code: int, headers: dict[str, str], content: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = content


def load_browser_cookies(settings: Settings) -> httpx.Cookies:
    import httpx

    jar = httpx.Cookies()
    if not settings.storage_state_path.exists():
        return jar

    data = json.loads(settings.storage_state_path.read_text(encoding="utf-8"))
    for cookie in data.get("cookies", []):
        domain = cookie.get("domain", "")
        if "expensify.com" not in domain:
            continue
        jar.set(
            cookie["name"],
            cookie["value"],
            domain=domain.lstrip("."),
            path=cookie.get("path", "/"),
        )
    return jar


def has_browser_session(settings: Settings) -> bool:
    if not settings.storage_state_path.exists():
        return False
    data = json.loads(settings.storage_state_path.read_text(encoding="utf-8"))
    names = {cookie.get("name") for cookie in data.get("cookies", [])}
    return bool(names & SESSION_COOKIE_NAMES)


def response_looks_like_receipt(response: httpx.Response | PlaywrightReceiptResponse) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith("image/") or "pdf" in content_type:
        return len(response.content) > 100
    return response.content[:4] == b"%PDF"


def probe_receipt_auth(
    settings: Settings,
    session: AuthSession,
    sample_url: str | Sequence[str],
) -> AuthStrategy | None:
    """Find a working auth strategy.

    Accepts several candidate receipts because an individual receipt can be
    permanently inaccessible (deleted, or owned by a workspace the account has
    left). Probing only one would report a perfectly good session as broken.
    """
    import httpx

    sample_urls = [sample_url] if isinstance(sample_url, str) else [u for u in sample_url if u]
    if not sample_urls:
        return None

    browser_cookies = load_browser_cookies(settings)
    header_token = session.encrypted_auth_token or session.auth_token
    strategies: list[tuple[AuthStrategy, httpx.Cookies, dict[str, str], str | None]] = [
        (
            AuthStrategy.COOKIE,
            httpx.Cookies({"authToken": session.auth_token}),
            dict(DEFAULT_RECEIPT_HEADERS),
            None,
        ),
        (
            AuthStrategy.HEADER,
            httpx.Cookies(),
            {**DEFAULT_RECEIPT_HEADERS, "X-Chat-Attachment-Token": header_token},
            None,
        ),
        (
            AuthStrategy.COOKIES_AND_HEADER,
            browser_cookies,
            {**DEFAULT_RECEIPT_HEADERS, "X-Chat-Attachment-Token": header_token},
            None,
        ),
        (AuthStrategy.BROWSER_COOKIES, browser_cookies, dict(DEFAULT_RECEIPT_HEADERS), None),
    ]

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for strategy, cookies, headers, url_override in strategies:
            if strategy == AuthStrategy.BROWSER_COOKIES and not has_browser_session(settings):
                continue
            if strategy == AuthStrategy.COOKIE and not session.auth_token:
                continue
            if strategy == AuthStrategy.HEADER and not (
                session.auth_token or session.encrypted_auth_token
            ):
                continue
            if strategy == AuthStrategy.COOKIES_AND_HEADER and not (
                session.auth_token or session.encrypted_auth_token
            ):
                continue
            for candidate in [url_override] if url_override else sample_urls:
                try:
                    response = client.get(candidate, cookies=cookies, headers=headers)
                except httpx.HTTPError:
                    continue
                if response.status_code == 200 and response_looks_like_receipt(response):
                    return strategy

    if settings.storage_state_path.exists():
        try:
            from expensify_scraper.browser_downloads import ReceiptBrowser

            with ReceiptBrowser(settings, session) as browser:
                for candidate in sample_urls:
                    if response_looks_like_receipt(browser.fetch(candidate)):
                        return AuthStrategy.PLAYWRIGHT
        except Exception:
            pass
    return None


def load_session(settings: Settings) -> AuthSession:
    if settings.token_path.exists():
        data = json.loads(settings.token_path.read_text(encoding="utf-8"))
        return AuthSession.from_dict(data)
    if has_browser_session(settings):
        from expensify_scraper.utils import utc_now_iso

        return AuthSession(auth_token="", captured_at=utc_now_iso())
    raise AuthError(
        f"No saved session at {settings.token_path}. Run: expensify-scraper auth"
    )


def save_session(settings: Settings, session: AuthSession) -> None:
    settings.ensure_dirs()
    settings.token_path.write_text(
        json.dumps(session.to_dict(), indent=2),
        encoding="utf-8",
    )
