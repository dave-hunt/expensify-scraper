# Contributing

Thanks for taking the time to contribute.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Lint and tests

```bash
ruff check src tests
pytest
```

Both gate CI. Ruff's configuration lives in `pyproject.toml`; line length is 100.

The suite is offline: it never contacts Expensify and never launches a browser.
Keep it that way. If you add behaviour that talks to the network, cover it with a
fake response rather than a live call, as `tests/test_core.py` does for
`mint_classic_tokens`.

Do not reference `.venv` paths in tests. CI runs on Linux, macOS contributors use
different layouts, and Windows uses `.venv\Scripts\`. Invoke the CLI through
`sys.executable` instead.

## Docker

```bash
docker build -t expensify-scraper .
docker run --rm expensify-scraper --help
```

`auth` needs a visible browser and only runs on the host. The image exists for
`download` and `verify`.

## Working with a real account

`auth`, `download`, `export`, and `verify` all act on a live Expensify account.
Before opening a pull request that touches them, run the flow end to end against
your own account and say so in the description; there is no fixture that can
stand in for this.

## Guidelines

- Target Python 3.11+ and keep the existing type annotations.
- Never commit `.env`, `.auth/`, `data/`, or `out/`. They hold live session
  tokens and real expense records, and they are gitignored for that reason.
- Expensify's Integration Server allows 5 requests per 10 seconds and 20 per 60
  seconds. Route new calls through `RateLimiter` rather than adding bare
  requests.
- Non-Python files the package reads at runtime must live under
  `src/expensify_scraper/` and be declared in `[tool.setuptools.package-data]`.
  An editable install finds files anywhere in the repo, so a missing declaration
  breaks only wheel installs. The `wheel` CI job guards this.
- Report bugs with the command you ran, the redacted output, and your OS and
  Python version. Never paste an `authToken`, a `partnerUserSecret`, or the
  contents of `.auth/`.
