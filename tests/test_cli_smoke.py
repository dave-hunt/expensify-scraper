from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLI = [sys.executable, "-m", "expensify_scraper.cli"]


def test_cli_help() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [*CLI, "--help"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    assert "export" in result.stdout
    assert "download" in result.stdout


def test_status_without_manifest_exits_nonzero(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["EXPENSIFY_DATA_DIR"] = str(tmp_path / "data")
    result = subprocess.run(
        [*CLI, "status"],
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
    )
    assert result.returncode != 0
    assert "No manifest yet" in result.stdout


def test_auth_reports_missing_chromium_without_a_traceback(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["EXPENSIFY_DATA_DIR"] = str(tmp_path / "data")
    env["EXPENSIFY_AUTH_DIR"] = str(tmp_path / ".auth")
    env["EXPENSIFY_OUTPUT_DIR"] = str(tmp_path / "out")
    # Point Playwright at an empty cache so the browser cannot be found. This is
    # the most common first-run failure and must not surface as a traceback.
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path / "no-browsers")

    result = subprocess.run(
        [*CLI, "auth"],
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "playwright install chromium" in output
    assert "Traceback" not in output
