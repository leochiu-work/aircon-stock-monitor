# Meaco Stock Monitor

Checks two Meaco portable air conditioners every five minutes and sends a Telegram message whenever either one can be purchased:

- [Meaco Cirro 12000 BTU Air Conditioner & Heater](https://www.meaco.com/products/meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner-heater)
- [Meaco Cirro 12000 BTU Air Conditioner](https://www.meaco.com/products/meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner)

The monitor is designed to run on GitHub Actions and uses only Python's standard library.

## How detection works

Meaco is a Shopify store. The monitor reads each product's public `.js` endpoint and treats it as in stock only when both conditions are true:

1. The product-level `available` field is `true`.
2. At least one variant has `available: true`.

This is the structured data behind the purchase UI, so no browser automation or screenshot comparison is needed.

When stock is available, one message lists every available monitored product with its current GBP price and direct purchase link. The workflow intentionally sends another alert on every five-minute run while stock remains available. It does not store history or deduplicate messages.

## Telegram setup

1. Open [BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and follow the prompts.
3. Keep the bot token private.
4. Open the new bot and send it a message such as `hello`. A bot cannot initiate a private conversation until you do this.
5. Obtain the chat ID from Telegram's `getUpdates` response. The following commands prompt for the token without displaying it:

   ```sh
   read -r -s "TELEGRAM_BOT_TOKEN?Bot token: "
   echo
   export TELEGRAM_BOT_TOKEN
   curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
   ```

   Find `result[].message.chat.id` in the returned JSON. Private chat IDs are normally positive numbers; group chat IDs are commonly negative.

6. Remove the token from the current shell when finished:

   ```sh
   unset TELEGRAM_BOT_TOKEN
   ```

## GitHub setup

Push this repository to GitHub, then add these repository Actions secrets under **Settings → Secrets and variables → Actions**:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

With the [GitHub CLI](https://cli.github.com/), both commands prompt securely for the values:

```sh
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
```

The workflow in `.github/workflows/monitor.yml` runs after it is committed to the repository's default branch. To test it immediately, open **Actions → Monitor Meaco stock → Run workflow**.

GitHub supports five minutes as the shortest scheduled-workflow interval. Scheduled jobs can be delayed during periods of high Actions load, so this is not a hard real-time guarantee. See [GitHub's scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

## Local use

Python 3.10 or newer is required. No packages need to be installed.

Run the test suite without any network access:

```sh
python3 -m unittest discover -s tests -v
```

Check the live Meaco endpoints without contacting Telegram:

```sh
python3 monitor.py --dry-run
```

Run a real check and allow Telegram notifications:

```sh
export TELEGRAM_BOT_TOKEN='your-bot-token'
export TELEGRAM_CHAT_ID='your-chat-id'
python3 monitor.py
```

Normal runs validate both environment variables before checking Meaco. A run exits with status `1` if product retrieval or Telegram delivery fails, making the failure visible in GitHub Actions.

## Troubleshooting

- **Missing environment variable:** Confirm both GitHub secret names exactly match the names above. Secret values are not available to workflows triggered from untrusted forks.
- **Telegram says `chat not found`:** Send the bot a private message first, then retrieve the chat ID again. For a group, add the bot to that group and use the group's chat ID.
- **Telegram says `bot was blocked by the user`:** Unblock the bot and send it a message.
- **Meaco checks fail after three attempts:** Open the product normally and inspect the Actions log. A temporary network or Meaco outage should resolve on a later scheduled run.
- **No run appears exactly every five minutes:** GitHub schedules are best-effort and can be delayed. Use the manual workflow trigger to check immediately.

Credentials are read from environment variables and are never written to files or included in monitor error messages.
