# Summary

<!-- What does this change and why? -->

## Testing

<!--
Which commands did you run? The test suite is offline, so if this touches
auth, download, export, or verify, say whether you ran it against a real
Expensify account (see CONTRIBUTING.md).
-->

- [ ] `ruff check src tests` passes
- [ ] `pytest` passes
- [ ] Ran against a real account (required for changes to `auth`, `download`, `export`, or `verify`)

## Checklist

- [ ] No secrets, tokens, receipt URLs, or real expense data in the diff or description
- [ ] No new files under `.env`, `.auth/`, `data/`, or `out/` are tracked
- [ ] New network calls go through `RateLimiter`
- [ ] README updated if behaviour or commands changed
