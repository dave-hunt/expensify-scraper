# Expensify Receipt Downloader

Bulk-download receipt files from an Expensify account into `out/YYYY/MM/`. There is no official “download all receipts” action; this CLI logs in once in a browser, lists expenses from Expensify’s live API, and saves the original attachments.

## How it works

Expensify's receipt files sit behind the same session that authenticates the web
app, so there is no API key that will fetch them on its own. The tool works in
three stages:

1. **`auth`** opens a real Chromium window and waits for you to sign in yourself.
   Your password and any magic link stay between you and Expensify. Once you are
   in, it saves the resulting browser session to `.auth/`.
2. **`download`** uses that saved session to mint short-lived API tokens the same
   way the official web app does, then asks Expensify's `Get` endpoint for your
   expense list and records it in a local SQLite manifest.
3. Each expense with an attachment is downloaded and filed under `out/YYYY/MM/`.
   Progress is written to the manifest as it goes, so re-running `download`
   resumes instead of starting over.

Nothing is uploaded anywhere, and no credentials leave your machine.

## Requirements

- Python 3.11+
- Chromium (installed by Playwright)
- An Expensify account you can sign in to interactively

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

On Windows, activate with `.venv\Scripts\activate`.

## Usage

**Step 1 — sign in once.**

```bash
expensify-scraper auth
```

A Chromium window opens at [new.expensify.com](https://new.expensify.com). Sign in
however you normally would, with a magic link or a password. The terminal waits
while you do this. Once your inbox is on screen — not the sign-in page — switch
back to the terminal and press Enter.

The command then saves your session to `.auth/`, remints API tokens from it, and
downloads a single receipt to confirm the whole path works. A successful run ends
with `Session OK for receipt downloads`.

**Step 2 — download everything.**

```bash
expensify-scraper download
```

This syncs your expense list and writes receipt files under `out/`. It is safe to
interrupt and re-run: anything already downloaded is skipped, so a second run
picks up where the first stopped.

**Step 3 — confirm the results.**

```bash
expensify-scraper verify
```

`verify` reconciles the manifest against the files actually on disk and writes
`out/summary.json`. `expensify-scraper status` prints the same counts without
writing anything, and `expensify-scraper run` does the download and the verify in
one go.

Sessions expire after a while. When receipt requests start returning 401 or 403,
run `expensify-scraper auth` again. If you only want to refresh tokens and re-test
without opening a login window, use `expensify-scraper auth --check`.

## Output

Receipts are named from the expense date (`modifiedCreated` if set, otherwise `created`):

```
out/YYYY/MM/YYYY-MM-DD_<merchant>_<amount><CURRENCY>_<receiptID>.<ext>
```

Local state (also gitignored):

| Path | Contents |
|------|----------|
| `out/` | Downloaded receipt files |
| `data/manifest.sqlite` | Expense and download status |
| `data/manifest.csv` | Same expenses as CSV |
| `out/summary.json` | Written by `verify` |
| `.auth/` | Browser cookies and API tokens |

Override paths with `EXPENSIFY_OUTPUT_DIR`, `EXPENSIFY_DATA_DIR`, or `EXPENSIFY_AUTH_DIR` in the environment or a local `.env`.

## Commands

| Command | Description |
|---------|-------------|
| `auth` | Headed login; saves session to `.auth/` |
| `auth --check` | Refresh tokens, sync expenses, probe one receipt |
| `download` | Sync expenses and download receipt files |
| `verify` | Reconcile the manifest against files on disk |
| `status` | Show expense and download counts |
| `run` | Download, then verify |
| `export` | Optional Classic report export (see below) |

## Limitations

- Expensify has no supported bulk-download API. This tool uses a browser session plus the same `Get` / `Authenticate` calls the web app uses.
- Some expenses have no image or PDF (eReceipts, map receipts, bank wire fees). Those are skipped.
- A receipt can have a URL that Expensify still refuses to serve. Those are listed
  individually under `skipped_downloads` in `out/summary.json`, since re-running
  `download` will not retrieve them.
- Receipts marked deleted in Expensify may still download if the API still returns a file URL.
- Sessions expire. Re-run `auth` when receipt requests return 401/403.

## Security

`.env`, `.auth/`, `data/`, and `out/` are gitignored and must stay that way. Each
holds something sensitive: `.auth/` is a live session and is equivalent to being
logged in to the account, `.env` holds your Integration Server secret, and `data/`
and `out/` hold your actual expense records and receipts.

If you fork this repository, check `git status` before your first commit. When
reporting a bug, never paste an `authToken`, a `partnerUserSecret`, or the
contents of `.auth/`.

To report a vulnerability, use
[private security advisories](https://github.com/dave-hunt/expensify-scraper/security/advisories/new)
rather than a public issue. See [SECURITY.md](SECURITY.md) for the full policy,
including which paths hold sensitive data and what is explicitly not a
vulnerability.

The `partnerName` and `partnerPassword` constants in
`src/expensify_scraper/classic_api.py` are not secrets. They are the public client
identifiers that Expensify ships in its own open-source app
([Expensify/App, `src/CONFIG.ts`](https://github.com/Expensify/App/blob/main/src/CONFIG.ts)),
and they are what let this tool authenticate the same way the web app does.

## Optional: Integration Server export

**Most people can skip this.** `auth` + `download` already covers personal New
Expensify accounts. `export` exists for expenses that live on Classic *reports*,
which the live API does not return, and it pulls them from Expensify's
[Integration Server](https://integrations.expensify.com/Integration-Server/doc/).

### Getting Integration Server credentials

1. Sign in to [expensify.com](https://www.expensify.com/) in your browser.
2. Visit **[expensify.com/tools/integrations](https://www.expensify.com/tools/integrations/)**.
   The page generates a `partnerUserID` and `partnerUserSecret` pair and displays
   them immediately — there is no form to fill in.
3. **Copy both before you leave the page.** Expensify does not show the secret
   again. If you lose it, return to the same page to generate a fresh pair.

The `partnerUserID` is derived from your account email and looks something like
`aa_yourname_example_com`. The `partnerUserSecret` is a 40-character hex string.
Treat the secret like a password: it authenticates as your account.

### Using them

```bash
cp .env.example .env
```

Uncomment and fill in the two values:

```
EXPENSIFY_PARTNER_USER_ID=aa_yourname_example_com
EXPENSIFY_PARTNER_USER_SECRET=your40characterhexsecret
```

Then export and download:

```bash
expensify-scraper export              # optionally --since 2020-01-01 --until 2023-12-31
expensify-scraper download
```

`export` walks the date range in one-year windows because the Integration Server
caps each query at 365 days, and it throttles itself to Expensify's published
limits of 5 requests per 10 seconds and 20 per 60 seconds.

## Docker

Log in on the host (`expensify-scraper auth`), then run download or verify in Compose. Auth needs a visible browser and is not run in the container.

```bash
docker compose run --rm download
docker compose run --rm verify
```

Compose mounts `./data`, `./out`, and `./.auth`. `.auth` is mounted writable because `download` remints and re-saves API tokens. The image bundles headless Chromium for that step.

## Troubleshooting

**`Executable doesn't exist … playwright install`** — Playwright's Chromium was
never downloaded into this virtualenv. Run `playwright install chromium` with the
virtualenv active.

**`Still on the sign-in page`** — `auth` checks the browser before saving. Finish
the login so your inbox is visible, then press Enter. If a magic link opened a
second tab, complete it in the window Playwright opened rather than your normal
browser.

**Downloads start returning 401 or 403** — the session expired. Run
`expensify-scraper auth` again. Already-downloaded receipts are kept.

**`No downloadable receipts found on this account yet`** — the manifest synced but
found no expense with a file attached. This is normal for accounts whose expenses
are all eReceipts, map receipts, or bank wire fees, none of which have an original
image or PDF.

**`Expensify Get failed` or repeated 429s** — Expensify is rate limiting. `export`
already throttles to the published limits; wait a minute and re-run. If you raised
`EXPENSIFY_DOWNLOAD_CONCURRENCY`, lower it back toward the default of 4.

**Counts do not add up in `status`** — run `expensify-scraper verify`, which
reconciles the manifest against the files on disk and lists anything missing in
`out/summary.json`.

## Development

```bash
source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest
expensify-scraper --help
```

The test suite is offline and never launches a browser. See
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

[MIT](LICENSE)
