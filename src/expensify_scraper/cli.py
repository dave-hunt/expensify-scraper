from __future__ import annotations

import json
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from expensify_scraper.auth import (
    AuthError,
    AuthSession,
    has_browser_session,
    load_session,
    login_and_capture,
    probe_receipt_auth,
    save_session,
)
from expensify_scraper.browser_downloads import refresh_session_tokens
from expensify_scraper.classic_api import ClassicAPIError, enrich_manifest_from_api
from expensify_scraper.config import Settings, get_settings
from expensify_scraper.downloader import run_download
from expensify_scraper.export import run_export
from expensify_scraper.manifest import ManifestStore
from expensify_scraper.receipt_urls import resolve_receipt_url_from_row
from expensify_scraper.verify import verify_downloads

app = typer.Typer(
    name="expensify-scraper",
    help="Bulk download Expensify receipt attachments organized by month/year.",
    no_args_is_help=True,
)
console = Console()


def _settings() -> Settings:
    try:
        settings = get_settings()
        settings.ensure_dirs()
        return settings
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def auth(
    check: bool = typer.Option(False, "--check", help="Validate stored auth token"),
) -> None:
    """Log in via browser and capture authToken for receipt downloads."""
    settings = _settings()
    if check:
        _check_auth(settings)
        return
    try:
        session = login_and_capture(settings)
    except AuthError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]Saved browser session[/green] to {settings.storage_state_path}"
    )
    if settings.token_path.exists():
        console.print(f"[green]Saved auth metadata[/green] to {settings.token_path}")
    _check_auth(settings, session=session)


@app.command()
def export(
    since: str | None = typer.Option(None, help="Start date YYYY-MM-DD"),
    until: str | None = typer.Option(None, help="End date YYYY-MM-DD"),
    limit: str | None = typer.Option(None, help="Max reports per window (testing)"),
) -> None:
    """Export Classic report expenses from the Integration Server (optional)."""
    settings = _settings()
    try:
        settings.require_integration_credentials()
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    since_date = date.fromisoformat(since) if since else None
    until_date = date.fromisoformat(until) if until else None
    run_export(settings, since=since_date, until=until_date, limit=limit)


@app.command()
def download() -> None:
    """Sync expenses from the live API and download receipt files."""
    settings = _settings()
    _check_auth(settings)
    with ManifestStore(settings.manifest_db_path) as store:
        stats = run_download(settings, store)
    console.print(f"[green]Download finished:[/green] {stats}")


@app.command()
def run() -> None:
    """Download receipts and verify completeness."""
    settings = _settings()
    _check_auth(settings)
    with ManifestStore(settings.manifest_db_path) as store:
        run_download(settings, store)
        summary = verify_downloads(settings, store)
    _print_summary(summary)


@app.command()
def verify() -> None:
    """Verify downloaded files against manifest and write summary.json."""
    settings = _settings()
    with ManifestStore(settings.manifest_db_path) as store:
        summary = verify_downloads(settings, store)
    _print_summary(summary)


@app.command()
def status() -> None:
    """Show manifest and download counts."""
    settings = _settings()
    if not settings.manifest_db_path.exists():
        console.print("[yellow]No manifest yet. Run: expensify-scraper auth[/yellow]")
        raise typer.Exit(code=1)
    with ManifestStore(settings.manifest_db_path) as store:
        counts = store.counts()
    table = Table(title="Expensify Receipt Downloader Status")
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)


def _check_auth(settings: Settings, session: AuthSession | None = None) -> AuthSession:
    try:
        session = session or load_session(settings)
    except AuthError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not has_browser_session(settings) and not session.auth_token:
        console.print(
            "[red]No browser session cookies found.[/red] Run: expensify-scraper auth"
        )
        raise typer.Exit(code=1)

    try:
        session = refresh_session_tokens(settings, session)
    except (AuthError, ClassicAPIError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    with ManifestStore(settings.manifest_db_path) as store:
        try:
            synced = enrich_manifest_from_api(store, session.auth_token)
        except ClassicAPIError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        if synced:
            store.export_csv(settings.manifest_csv_path)
            console.print(f"[green]Synced[/green] {synced} expenses from Expensify API")
        sample = store.probe_candidates()
        if not sample:
            console.print("[yellow]No downloadable receipts found on this account yet.[/yellow]")
            return session
        sample_urls = [
            url
            for record in sample
            if (url := resolve_receipt_url_from_row(record.receipt_url, record.raw))
        ]
        if not sample_urls:
            console.print("[yellow]No resolvable receipt URL in manifest yet.[/yellow]")
            return session

    strategy = probe_receipt_auth(settings, session, sample_urls)
    if strategy is None:
        console.print(
            f"[red]Could not download any of {len(sample_urls)} sample receipts "
            "with the saved session.[/red]\n"
            "The session has most likely expired. Run: expensify-scraper auth"
        )
        raise typer.Exit(code=1)


    console.print(f"[green]Session OK[/green] for receipt downloads via {strategy.value}")
    session.strategy = strategy
    save_session(settings, session)
    return session


def _print_summary(summary: dict) -> None:
    counts = summary.get("counts", {})
    complete = summary.get("complete", False)
    color = "green" if complete else "yellow"
    console.print(f"[{color}]Verification complete[/{color}]")
    console.print(json.dumps(counts, indent=2))
    if summary.get("failed_downloads"):
        console.print(f"[red]Failed:[/red] {len(summary['failed_downloads'])}")
    if summary.get("missing_files"):
        console.print(f"[red]Missing files:[/red] {len(summary['missing_files'])}")
    if summary.get("skipped_downloads"):
        console.print(
            f"[yellow]Receipts Expensify would not serve:[/yellow] "
            f"{len(summary['skipped_downloads'])} (listed in summary.json)"
        )


if __name__ == "__main__":
    app()
