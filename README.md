# Meaco Stock Monitor

Checks two Meaco portable air conditioners every two minutes and sends a Telegram message whenever either one can be purchased:

- [Meaco Cirro 12000 BTU Air Conditioner & Heater](https://www.meaco.com/products/meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner-heater)
- [Meaco Cirro 12000 BTU Air Conditioner](https://www.meaco.com/products/meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner)

The monitor is designed to run on AWS Lambda, triggered every two minutes by
EventBridge Scheduler. It uses only Python's standard library.

## How detection works

Meaco is a Shopify store. The monitor reads each product's public `.js` endpoint and treats it as in stock only when both conditions are true:

1. The product-level `available` field is `true`.
2. At least one variant has `available: true`.

This is the structured data behind the purchase UI, so no browser automation or screenshot comparison is needed.

When stock is available, one message lists every available monitored product with its current GBP price and direct purchase link. The monitor intentionally sends another alert on every two-minute run while stock remains available. It does not store history or deduplicate messages.

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

## AWS deployment

The deployment uses AWS SAM. AWS CLI credentials and the SAM CLI must be configured
locally. Deploy with:

```sh
sam build
read -r -s "TELEGRAM_BOT_TOKEN?Bot token: "
echo
read -r "TELEGRAM_CHAT_ID?Chat ID: "
sam deploy \
  --stack-name aircon-stock-monitor \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "TelegramBotToken=${TELEGRAM_BOT_TOKEN}" \
    "TelegramChatId=${TELEGRAM_CHAT_ID}"
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
```

The template uses `rate(2 minutes)` and disables EventBridge Scheduler's flexible
delivery window. To change the interval, pass `ScheduleExpression` as an additional
parameter override. The Telegram values are stored as encrypted Lambda environment
variables and are not committed to the repository.

After deployment, invoke one immediate check and inspect its logs:

```sh
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name aircon-stock-monitor \
  --query 'Stacks[0].Outputs[?OutputKey==`FunctionName`].OutputValue' \
  --output text)
aws lambda invoke --function-name "$FUNCTION_NAME" /tmp/aircon-result.json
aws logs tail "/aws/lambda/${FUNCTION_NAME}" --since 10m
```

To verify Telegram delivery without waiting for real stock, invoke the explicit test
event. It sends one clearly labelled test notification and does not run a stock check:

```sh
aws lambda invoke \
  --function-name "$FUNCTION_NAME" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"test_notification":true}' \
  /tmp/aircon-test-result.json
```

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

- **Missing environment variable:** Confirm the deployed Lambda function has both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` configured.
- **Telegram says `chat not found`:** Send the bot a private message first, then retrieve the chat ID again. For a group, add the bot to that group and use the group's chat ID.
- **Telegram says `bot was blocked by the user`:** Unblock the bot and send it a message.
- **Meaco checks fail after three attempts:** Open the product normally and inspect the CloudWatch log. A temporary network or Meaco outage should resolve on a later scheduled run.

Credentials are read from environment variables and are never written to files or included in monitor error messages.
