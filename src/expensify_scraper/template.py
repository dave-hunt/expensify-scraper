from __future__ import annotations

import csv
import io
from pathlib import Path

from expensify_scraper.utils import parse_ndjson_lines


def load_template(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def parse_csv_manifest(content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content))
    rows: list[dict] = []
    for row in reader:
        cleaned = {key: (value if value != "" else None) for key, value in row.items()}
        rows.append(cleaned)
    return rows


def parse_manifest_content(content: str) -> list[dict]:
    stripped = content.strip()
    if not stripped:
        return []
    if stripped.startswith("{"):
        return parse_ndjson_lines(content)
    return parse_csv_manifest(content)
