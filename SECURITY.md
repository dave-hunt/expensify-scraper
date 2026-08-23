# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub's private vulnerability
reporting: open the
[Security tab](https://github.com/dave-hunt/expensify-scraper/security/advisories/new)
and file a draft advisory. Do not open a public issue for a vulnerability.

Please include what you were doing, what happened, and how to reproduce it. You
should get an initial response within a few days.

### Never include secrets in a report

This tool handles live Expensify session material. When reporting anything —
security issue or ordinary bug — never paste:

- an `authToken` or `encryptedAuthToken`
- a `partnerUserSecret`
- the contents of `.auth/storage_state.json` or `.auth/token.json`
- cookie values, or full receipt URLs (they contain account-scoped identifiers)

Redact them. A stack trace with the token replaced by `<redacted>` is far more
useful than one you have to take down later.

## What this tool touches

Understanding the trust model helps when judging whether something is a
vulnerability:

| Path | Sensitivity | Notes |
|------|-------------|-------|
| `.auth/storage_state.json` | **Critical** | Browser cookies. Possessing this is equivalent to being signed in to the account. |
| `.auth/token.json` | **Critical** | Expensify API tokens and the auto-generated login pair used to remint them. |
| `.env` | **High** | Holds the Integration Server `partnerUserSecret`, which authenticates as your account. |
| `data/manifest.sqlite`, `data/manifest.csv` | **Medium** | Merchant names, amounts, categories, and receipt URLs. |
| `out/` | **Medium** | The receipt images and PDFs themselves. |

All five are gitignored and must stay that way. Nothing is transmitted anywhere
except to Expensify's own endpoints; there is no telemetry and no third-party
service in the path.

### Not vulnerabilities

- **The `partnerName` / `partnerPassword` constants in
  `src/expensify_scraper/classic_api.py`.** These are public client identifiers
  that Expensify ships in its own open-source app
  ([Expensify/App, `src/CONFIG.ts`](https://github.com/Expensify/App/blob/main/src/CONFIG.ts)).
  They are not user credentials and grant nothing on their own.
- **Credentials stored unencrypted in `.auth/`.** This is a local-only CLI that
  relies on filesystem permissions, the same as any `~/.aws/credentials` style
  tool. Protecting them at rest is the operating system's job.
- **Reports that the tool can read your own expense data.** That is its purpose.

## Supported versions

This project is pre-1.0. Fixes land on `main`, and only the latest release is
supported.

## Session hygiene

- Sessions expire; re-run `expensify-scraper auth` rather than trying to extend one.
- To revoke access, sign out of all sessions in Expensify's account settings and
  delete your local `.auth/` directory.
- If you generated Integration Server credentials you no longer use, return to
  [expensify.com/tools/integrations](https://www.expensify.com/tools/integrations/)
  and generate a fresh pair to rotate the old secret out.
- Treat `out/` and `data/` as you would a folder of scanned receipts, because
  that is exactly what they are.
