from __future__ import annotations

from datetime import date

from expensify_scraper.manifest import ExpenseRecord, ManifestStore
from expensify_scraper.organizer import build_receipt_path, guess_extension, merchant_slug
from expensify_scraper.utils import iter_year_windows, parse_ndjson_lines


def test_settings_work_without_partner_credentials(monkeypatch, tmp_path) -> None:
    from expensify_scraper.config import Settings

    monkeypatch.delenv("EXPENSIFY_PARTNER_USER_ID", raising=False)
    monkeypatch.delenv("EXPENSIFY_PARTNER_USER_SECRET", raising=False)
    settings = Settings(
        _env_file=None,
        EXPENSIFY_OUTPUT_DIR=tmp_path / "out",
        EXPENSIFY_DATA_DIR=tmp_path / "data",
        EXPENSIFY_AUTH_DIR=tmp_path / ".auth",
    )
    assert settings.expensify_partner_user_id is None
    try:
        settings.require_integration_credentials()
    except ValueError as exc:
        assert "export requires" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_iter_year_windows_respects_one_year_cap() -> None:
    windows = iter_year_windows(date(2020, 1, 1), date(2022, 6, 1))
    assert len(windows) == 3
    assert windows[0].start == date(2020, 1, 1)
    assert windows[0].end == date(2020, 12, 30)


def test_parse_ndjson_lines() -> None:
    content = '{"a":1}\n{"b":2}\n'
    rows = parse_ndjson_lines(content)
    assert rows == [{"a": 1}, {"b": 2}]


def test_merchant_slug() -> None:
    assert merchant_slug("Joe's Coffee & Tea!") == "joe-s-coffee-tea"


def test_guess_extension() -> None:
    assert guess_extension("image/jpeg", "https://x/receipt") == ".jpg"
    assert guess_extension(None, "https://x/file.PDF") == ".pdf"


def test_manifest_store_roundtrip(tmp_path) -> None:
    db = tmp_path / "manifest.sqlite"
    row = ExpenseRecord.from_row(
        {
            "expenseID": "1",
            "reportID": "10",
            "reportName": "Trip",
            "created": "2024-03-01",
            "modifiedCreated": "",
            "merchant": "Test Merchant",
            "amount": 1234,
            "currency": "USD",
            "category": "Meals",
            "receiptID": "R1",
            "receiptURL": "https://example.com/r1",
        }
    )
    with ManifestStore(db) as store:
        store.upsert_expenses([row])
        downloadable = store.iter_downloadable()
        assert len(downloadable) == 1
        store.set_download(
            "R1",
            expense_id="1",
            status=__import__(
                "expensify_scraper.manifest", fromlist=["DownloadStatus"]
            ).DownloadStatus.COMPLETE,
            local_path=str(tmp_path / "file.jpg"),
        )
        counts = store.counts()
        assert counts["complete"] == 1


def test_export_csv_writes_column_names(tmp_path) -> None:
    import csv

    row = ExpenseRecord.from_row(
        {
            "expenseID": "1",
            "reportID": "10",
            "created": "2024-03-01",
            "merchant": "Test Merchant",
            "amount": 500,
            "currency": "USD",
            "receiptID": "R1",
            "receiptURL": "https://example.com/r1.jpg",
        }
    )
    csv_path = tmp_path / "manifest.csv"
    with ManifestStore(tmp_path / "manifest.sqlite") as store:
        store.upsert_expenses([row])
        store.export_csv(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header[0] == "expense_id"
    assert "merchant" in header


def test_resolve_receipt_url_from_filename() -> None:
    from expensify_scraper.receipt_urls import resolve_receipt_url_from_row

    record = ExpenseRecord.from_row(
        {
            "receiptID": "123",
            "receiptURL": None,
            "receiptFilename": "w_abc123.jpg",
            "created": "2024-01-01",
        }
    )
    assert (
        resolve_receipt_url_from_row(record.receipt_url, record.raw)
        == "https://www.expensify.com/receipts/w_abc123.jpg"
    )


def test_resolve_prefers_receipt_object_url() -> None:
    from expensify_scraper.receipt_urls import resolve_receipt_url_from_row

    url = resolve_receipt_url_from_row(
        "https://www.expensify.com/receipts/w_abc123.pdf",
        {
            "receiptFilename": "w_abc123.pdf",
            "receiptObject": {
                "url": "https://www.expensify.com/receipts/u_xyz789.pdf"
            },
        },
    )
    assert url == "https://www.expensify.com/receipts/u_xyz789.pdf"


def test_mint_rejects_error_payload(monkeypatch) -> None:
    from expensify_scraper import classic_api

    class FakeResponse:
        def json(self) -> dict:
            return {
                "jsonCode": 402,
                "message": "The given email is not valid.",
                "data": {"token": "A" * 768},
            }

    monkeypatch.setattr(classic_api.httpx, "post", lambda *a, **k: FakeResponse())
    try:
        classic_api.mint_classic_tokens("expensify.cash-test", "secret")
    except classic_api.ClassicAPIError as exc:
        assert "402" in str(exc)
    else:
        raise AssertionError("expected ClassicAPIError")


def test_mint_reads_json_code_200(monkeypatch) -> None:
    from expensify_scraper import classic_api

    class FakeResponse:
        def json(self) -> dict:
            return {
                "jsonCode": 200,
                "authToken": "T" * 32,
                "encryptedAuthToken": "E" * 16,
            }

    monkeypatch.setattr(classic_api.httpx, "post", lambda *a, **k: FakeResponse())
    tokens = classic_api.mint_classic_tokens("expensify.cash-test", "secret")
    assert tokens.auth_token == "T" * 32
    assert tokens.encrypted_auth_token == "E" * 16


def test_expense_row_from_transaction_uses_object_url() -> None:
    from expensify_scraper.classic_api import expense_row_from_transaction
    from expensify_scraper.receipt_urls import resolve_receipt_url_from_row

    row = expense_row_from_transaction(
        {
            "transactionID": "99",
            "created": "2024-01-01",
            "receiptID": 12,
            "receiptFilename": "w_old.pdf",
            "receiptObject": {"url": "https://www.expensify.com/receipts/u_new.pdf"},
            "amount": 100,
            "currency": "USD",
        }
    )
    assert (
        resolve_receipt_url_from_row(row.get("receiptURL"), row)
        == "https://www.expensify.com/receipts/u_new.pdf"
    )


def test_extract_token_from_request_body() -> None:
    from expensify_scraper.auth import _extract_token_from_request

    token = "A" * 32
    assert _extract_token_from_request(f'{{"authToken":"{token}"}}') == token
    assert _extract_token_from_request(None) is None


def test_parse_csv_manifest() -> None:
    from expensify_scraper.template import parse_manifest_content

    content = (
        "transactionID,merchant,receiptID\n"
        '"123","Coffee","456"\n'
    )
    rows = parse_manifest_content(content)
    assert rows[0]["transactionID"] == "123"
    assert rows[0]["merchant"] == "Coffee"


def test_build_receipt_path(tmp_path) -> None:
    expense = ExpenseRecord.from_row(
        {
            "expenseID": "1",
            "reportID": "10",
            "created": "2024-03-15",
            "merchant": "Acme Corp",
            "amount": 5000,
            "currency": "USD",
            "receiptID": "abc123",
            "receiptURL": "https://example.com/r",
        }
    )
    path = build_receipt_path(tmp_path, expense, ".jpg")
    assert path.parent.name == "03"
    assert path.parent.parent.name == "2024"
    assert path.name.endswith("_abc123.jpg")


def test_parse_expense_date_tolerates_unparseable_value() -> None:
    from expensify_scraper.utils import parse_expense_date

    assert parse_expense_date({"created": "2024-03-15"}) == date(2024, 3, 15)
    # A malformed date must not raise, or one bad row would abort the whole sync.
    assert parse_expense_date({"created": "not-a-date"}) == date.today()


def test_build_receipt_path_stays_inside_output_dir(tmp_path) -> None:
    expense = ExpenseRecord.from_row(
        {
            "expenseID": "1",
            "reportID": "10",
            "created": "2024-03-15",
            "merchant": "../../etc/passwd",
            "amount": 100,
            "currency": "../../..",
            "receiptID": "../../secret",
            "receiptURL": "https://example.com/r",
        }
    )
    path = build_receipt_path(tmp_path, expense, ".jpg")
    assert tmp_path.resolve() in path.resolve().parents
    assert ".." not in path.name


def _expense(receipt_id: str, day: str = "01") -> ExpenseRecord:
    return ExpenseRecord.from_row(
        {
            "expenseID": f"e{receipt_id}",
            "reportID": "10",
            "created": f"2024-03-{day}",
            "merchant": "Acme",
            "amount": 100,
            "currency": "USD",
            "receiptID": receipt_id,
            "receiptURL": f"https://example.com/{receipt_id}.pdf",
        }
    )


def test_complete_receipt_is_requeued_when_its_file_is_gone(tmp_path) -> None:
    from expensify_scraper.manifest import DownloadStatus

    receipt = tmp_path / "r1.pdf"
    receipt.write_bytes(b"%PDF-1.4 fake")
    with ManifestStore(tmp_path / "m.sqlite") as store:
        store.upsert_expenses([_expense("R1")])
        store.set_download(
            "R1", expense_id="eR1", status=DownloadStatus.COMPLETE, local_path=str(receipt)
        )
        assert store.iter_downloadable() == []

        # Deleting out/ must not leave the tool reporting nothing to do.
        receipt.unlink()
        assert [r.receipt_id for r in store.iter_downloadable()] == ["R1"]


def test_skipped_receipts_are_not_requeued(tmp_path) -> None:
    from expensify_scraper.manifest import DownloadStatus

    with ManifestStore(tmp_path / "m.sqlite") as store:
        store.upsert_expenses([_expense("R1")])
        store.set_download("R1", expense_id="eR1", status=DownloadStatus.SKIPPED)
        assert store.iter_downloadable() == []


def test_probe_candidates_prefer_receipts_known_to_work(tmp_path) -> None:
    from expensify_scraper.manifest import DownloadStatus

    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.4 fake")
    with ManifestStore(tmp_path / "m.sqlite") as store:
        store.upsert_expenses([_expense("GOOD", "01"), _expense("STUCK", "02")])
        store.set_download(
            "GOOD", expense_id="eGOOD", status=DownloadStatus.COMPLETE, local_path=str(good)
        )
        store.set_download(
            "STUCK", expense_id="eSTUCK", status=DownloadStatus.FAILED, error="403"
        )
        candidates = [r.receipt_id for r in store.probe_candidates()]

    # A permanently inaccessible receipt must not be the only thing probed, or a
    # healthy session gets reported as broken.
    assert candidates[0] == "GOOD"
    assert "STUCK" in candidates


def test_probe_receipt_auth_accepts_multiple_candidates(tmp_path) -> None:
    from expensify_scraper.auth import AuthSession, probe_receipt_auth
    from expensify_scraper.config import Settings

    settings = Settings(
        _env_file=None,
        EXPENSIFY_OUTPUT_DIR=tmp_path / "out",
        EXPENSIFY_DATA_DIR=tmp_path / "data",
        EXPENSIFY_AUTH_DIR=tmp_path / ".auth",
    )
    # No candidates and no saved state: must report failure rather than raise.
    assert probe_receipt_auth(settings, AuthSession(auth_token=""), []) is None


def test_verify_reports_receipts_expensify_would_not_serve(tmp_path) -> None:
    from expensify_scraper.config import Settings
    from expensify_scraper.manifest import DownloadStatus
    from expensify_scraper.verify import verify_downloads

    settings = Settings(
        _env_file=None,
        EXPENSIFY_OUTPUT_DIR=tmp_path / "out",
        EXPENSIFY_DATA_DIR=tmp_path / "data",
        EXPENSIFY_AUTH_DIR=tmp_path / ".auth",
    )
    got = tmp_path / "got.pdf"
    got.write_bytes(b"%PDF-1.4 fake")
    with ManifestStore(tmp_path / "m.sqlite") as store:
        store.upsert_expenses([_expense("GOT", "01"), _expense("REFUSED", "02")])
        store.set_download(
            "GOT", expense_id="eGOT", status=DownloadStatus.COMPLETE, local_path=str(got)
        )
        store.set_download(
            "REFUSED", expense_id="eREFUSED", status=DownloadStatus.SKIPPED, error="HTTP 403"
        )
        summary = verify_downloads(settings, store)

    # An inaccessible receipt must be named, not silently folded in with the
    # eReceipts that never had a file to begin with.
    skipped = summary["skipped_downloads"]
    assert [row["receipt_id"] for row in skipped] == ["REFUSED"]
    assert skipped[0]["error"] == "HTTP 403"
    assert skipped[0]["merchant"] == "Acme"

    # Nothing is left to retry, so the run still counts as complete.
    assert summary["complete"] is True


def test_template_ships_as_package_data() -> None:
    from expensify_scraper.config import Settings
    from expensify_scraper.template import load_template

    # Guards against the template being unreachable in a non-editable install.
    path = Settings(_env_file=None).template_path
    assert path.exists(), f"template missing at {path}"
    assert "transactionID" in load_template(path)
