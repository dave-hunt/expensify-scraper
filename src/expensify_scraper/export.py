from __future__ import annotations

from datetime import date

from rich.console import Console

from expensify_scraper.config import Settings
from expensify_scraper.integration_api import IntegrationServerClient
from expensify_scraper.manifest import ExpenseRecord, ManifestStore
from expensify_scraper.template import load_template, parse_manifest_content

console = Console()


def run_export(
    settings: Settings,
    *,
    since: date | None = None,
    until: date | None = None,
    limit: str | None = None,
) -> int:
    settings.ensure_dirs()
    template = load_template(settings.template_path)

    total_imported = 0
    with IntegrationServerClient(settings) as client:
        if limit is not None:
            from datetime import timedelta

            end = until or date.today()
            start = since or (end - timedelta(days=364))
            result = client.export_combined_report(
                template,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                limit=limit,
            )
            results = [result]
        else:
            results = client.export_all_windows(template, since=since, until=until)

        with ManifestStore(settings.manifest_db_path) as store:
            for result in results:
                window = result.window
                label = (
                    f"{window.start}..{window.end}" if window else result.filename
                )
                rows = parse_manifest_content(result.content)
                records = [ExpenseRecord.from_row(row) for row in rows]
                imported = store.upsert_expenses(records)
                total_imported += imported
                console.print(
                    f"[green]Imported[/green] {imported} expenses from {label}"
                )
            store.export_csv(settings.manifest_csv_path)

    console.print(
        f"[bold green]Export complete[/bold green]: {total_imported} expense rows, "
        f"manifest at {settings.manifest_db_path}"
    )
    return total_imported
