# Meaco Stock Monitor Implementation Plan

## Goal

Build a lightweight stock monitor for these products:

- [Meaco Cirro 12000 BTU Air Conditioner & Heater](https://www.meaco.com/products/meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner-heater)
- [Meaco Cirro 12000 BTU Air Conditioner](https://www.meaco.com/products/meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner)

Run the monitor through GitHub Actions every five minutes. Whenever either product is purchasable, send a Telegram alert on every check with its name, price, and direct purchase link. No dashboard is required.

## Technical Approach

Use Python and the standard library only. Query each Shopify product endpoint by appending `.js` to its product URL. These endpoints expose top-level and per-variant `available` fields and avoid brittle browser automation, rendered-text matching, or screenshots.

A product is considered in stock only when:

1. The top-level `available` value is `true`.
2. At least one variant also has `available: true`.

The monitor will:

- Check both products independently with a 15-second timeout.
- Retry transient HTTP, timeout, and malformed-JSON failures three times with short backoff.
- Continue checking the second product if the first one fails.
- Combine all available products into one Telegram message per run.
- Send an alert on every scheduled check while stock remains available.
- Exit successfully when checks complete and nothing is in stock.
- Exit non-zero after reporting any product-fetch or Telegram error so GitHub marks the run as failed.
- Provide `--dry-run` to print the proposed alert without contacting Telegram.

## Files to Implement

### `monitor.py`

- Define the two monitored product URLs as immutable configuration.
- Fetch and validate Shopify product JSON.
- Represent normalized product status with a dataclass containing title, URL, price, and availability.
- Format integer Shopify prices as GBP.
- Build a plain-text Telegram alert so product URLs remain directly clickable.
- Send alerts through Telegram's Bot API using an HTTP POST request.
- Read `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from environment variables.
- Never print credentials or include the bot token in surfaced error messages.
- Validate credentials before a normal run; bypass credential requirements in dry-run mode.

### `tests/test_monitor.py`

Use `unittest` and mocks; tests must not contact Meaco or Telegram.

Cover:

- Both products unavailable: no Telegram call and successful exit.
- One available product: one correctly formatted alert.
- Both products available: one combined alert containing both links.
- Top-level and variant availability disagreement: no false positive.
- Timeout, HTTP failure, malformed JSON, and missing required fields: retry and fail visibly.
- One product fails while the other is available: send the valid alert, then return failure.
- Telegram rejects or times out: return failure without exposing the token.
- Dry-run mode: print the alert and make no Telegram request.
- GBP price formatting.

### `.github/workflows/monitor.yml`

- Trigger on `schedule` with `*/5 * * * *` and on `workflow_dispatch` for manual checks.
- Use read-only repository permissions.
- Use workflow concurrency with cancellation of an older overlapping run.
- Set a short job timeout.
- Check out the repository, install a supported Python runtime, and run `monitor.py`.
- Pass `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from GitHub Actions secrets.

### `README.md`

Document:

- How stock detection works.
- Local dry-run and test commands.
- How to create a Telegram bot with BotFather.
- How to send the bot an initial message and obtain the target chat ID.
- How to create the two GitHub repository secrets.
- How to enable and manually invoke the workflow.
- GitHub Actions scheduling limitations: five minutes is the minimum interval, but jobs can start late during platform congestion.
- Troubleshooting for failed requests, invalid secrets, and Telegram permissions.

## Acceptance Criteria

- The monitor detects the known out-of-stock payloads as unavailable.
- A fixture matching the reference product's enabled Add-to-cart state is detected as available.
- Available products produce a Telegram alert containing their current title, GBP price, and direct URL.
- Alerts repeat on every five-minute run while availability remains true.
- An unavailable product never causes a stock notification.
- All tests pass without network access or third-party packages.
- Secrets exist only in GitHub Actions configuration and are never committed or logged.

## Assumptions

- The repository will be hosted on GitHub with Actions enabled.
- The user will configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` before enabling the scheduled workflow.
- Meaco continues exposing its public Shopify `.js` product endpoints.
- If Meaco removes or materially changes those endpoints, HTML parsing will be added as a fallback in a later change.
- GitHub Actions is acceptable even though scheduled starts are not guaranteed to occur at the exact cron minute.

## Deferred Work

- Do not implement a web dashboard.
- Do not persist availability history or deduplicate alerts.
- Do not add email notifications.
- Do not use browser automation unless the structured Shopify endpoint stops working.
