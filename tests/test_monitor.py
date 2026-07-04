from __future__ import annotations

import io
import json
import signal
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import monitor


class FakeResponse:
    def __init__(self, payload: object):
        self.body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def product_payload(*, available: bool, variant_available: bool, title: str = "Test AC") -> dict:
    return {
        "title": title,
        "price": 51999,
        "available": available,
        "variants": [{"id": 123, "available": variant_available}],
    }


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = monitor.Product("First", "https://shop.test/products/first")
        self.second = monitor.Product("Second", "https://shop.test/products/second")
        self.credentials = monitor.TelegramCredentials("secret-token", "12345")

    def status(self, product: monitor.Product, *, available: bool) -> monitor.ProductStatus:
        return monitor.ProductStatus(product.name, product.page_url, 51999, available)

    def run_with(self, fetcher, notifier=lambda *_args: None, *, dry_run=False):
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = monitor.run_monitor(
            products=(self.first, self.second),
            fetcher=fetcher,
            notifier=notifier,
            credentials=None if dry_run else self.credentials,
            dry_run=dry_run,
            stdout=stdout,
            stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_both_products_unavailable_send_no_alert(self) -> None:
        notifications = []
        code, stdout, stderr = self.run_with(
            lambda product: self.status(product, available=False),
            lambda *args: notifications.append(args),
        )
        self.assertEqual(code, 0)
        self.assertEqual(notifications, [])
        self.assertIn("No monitored products", stdout)
        self.assertEqual(stderr, "")

    def test_one_available_product_sends_formatted_alert(self) -> None:
        notifications = []
        code, _, _ = self.run_with(
            lambda product: self.status(product, available=product == self.first),
            lambda credentials, text: notifications.append((credentials, text)),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][0], self.credentials)
        self.assertIn("First", notifications[0][1])
        self.assertIn("£519.99", notifications[0][1])
        self.assertIn(self.first.page_url, notifications[0][1])
        self.assertNotIn(self.second.page_url, notifications[0][1])

    def test_two_available_products_are_combined_into_one_alert(self) -> None:
        notifications = []
        code, _, _ = self.run_with(
            lambda product: self.status(product, available=True),
            lambda _, text: notifications.append(text),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(notifications), 1)
        self.assertIn(self.first.page_url, notifications[0])
        self.assertIn(self.second.page_url, notifications[0])

    def test_product_and_variant_must_both_be_available(self) -> None:
        for product_available, variant_available in ((True, False), (False, True), (False, False)):
            with self.subTest(product=product_available, variant=variant_available):
                status = monitor.parse_product(
                    self.first,
                    product_payload(
                        available=product_available,
                        variant_available=variant_available,
                    ),
                )
                self.assertFalse(status.available)
        self.assertTrue(
            monitor.parse_product(
                self.first,
                product_payload(available=True, variant_available=True),
            ).available
        )

    def test_fetch_retries_timeout_http_and_malformed_json(self) -> None:
        results = [
            TimeoutError(),
            HTTPError("https://shop.test", 503, "Unavailable", {}, None),
            FakeResponse(b"not-json"),
        ]
        sleeps = []

        def opener(*_args, **_kwargs):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with self.assertRaisesRegex(monitor.MonitorError, "failed after 3 attempts"):
            monitor.fetch_product(self.first, opener=opener, sleep=sleeps.append)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_fetch_retries_missing_fields_then_succeeds(self) -> None:
        responses = [
            FakeResponse({"title": "Incomplete"}),
            FakeResponse(product_payload(available=False, variant_available=False)),
        ]
        sleeps = []

        status = monitor.fetch_product(
            self.first,
            opener=lambda *_args, **_kwargs: responses.pop(0),
            sleep=sleeps.append,
        )
        self.assertFalse(status.available)
        self.assertEqual(sleeps, [1.0])

    @unittest.skipUnless(hasattr(signal, "SIGALRM"), "requires Unix signals")
    def test_fetch_hard_timeout_bounds_slow_dns_or_connect(self) -> None:
        def slow_opener(*_args, **_kwargs):
            time.sleep(1)
            return FakeResponse(product_payload(available=False, variant_available=False))

        started = time.monotonic()
        with self.assertRaisesRegex(monitor.MonitorError, "TimeoutError"):
            monitor.fetch_product(
                self.first,
                timeout=0.01,
                attempts=1,
                opener=slow_opener,
                sleep=lambda _: None,
            )
        self.assertLess(time.monotonic() - started, 0.5)

    def test_one_failure_does_not_suppress_other_product_alert(self) -> None:
        notifications = []

        def fetcher(product):
            if product == self.first:
                raise monitor.MonitorError("unavailable after retries")
            return self.status(product, available=True)

        code, _, stderr = self.run_with(fetcher, lambda _, text: notifications.append(text))
        self.assertEqual(code, 1)
        self.assertEqual(len(notifications), 1)
        self.assertIn(self.second.page_url, notifications[0])
        self.assertIn("First", stderr)

    def test_telegram_rejection_is_reported(self) -> None:
        response = FakeResponse({"ok": False, "description": "chat not found"})
        with self.assertRaisesRegex(monitor.MonitorError, "chat not found"):
            monitor.send_telegram_message(
                self.credentials,
                "hello",
                opener=lambda *_args, **_kwargs: response,
            )

    def test_telegram_timeout_does_not_expose_token(self) -> None:
        with self.assertRaises(monitor.MonitorError) as caught:
            monitor.send_telegram_message(
                self.credentials,
                "hello",
                opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
            )
        self.assertNotIn(self.credentials.bot_token, str(caught.exception))

    def test_dry_run_prints_alert_without_notifying(self) -> None:
        notifications = []
        code, stdout, stderr = self.run_with(
            lambda product: self.status(product, available=product == self.first),
            lambda *args: notifications.append(args),
            dry_run=True,
        )
        self.assertEqual(code, 0)
        self.assertEqual(notifications, [])
        self.assertIn("Dry-run alert", stdout)
        self.assertIn(self.first.page_url, stdout)
        self.assertEqual(stderr, "")

    def test_price_formatting(self) -> None:
        self.assertEqual(monitor.format_gbp(0), "£0.00")
        self.assertEqual(monitor.format_gbp(51999), "£519.99")
        self.assertEqual(monitor.format_gbp(123456789), "£1,234,567.89")

    def test_missing_credentials_fail_before_checks(self) -> None:
        with patch.dict(monitor.os.environ, {}, clear=True):
            stdout = io.StringIO()
            stderr = io.StringIO()
            code = monitor.run_monitor(
                products=(self.first,),
                fetcher=lambda _: self.fail("fetch should not run"),
                stdout=stdout,
                stderr=stderr,
            )
        self.assertEqual(code, 1)
        self.assertIn("TELEGRAM_BOT_TOKEN", stderr.getvalue())
        self.assertIn("TELEGRAM_CHAT_ID", stderr.getvalue())

    @patch("monitor.run_monitor", return_value=0)
    def test_lambda_handler_returns_success(self, run_monitor) -> None:
        self.assertEqual(monitor.lambda_handler({}, None), {"status": "ok"})
        run_monitor.assert_called_once_with()

    @patch("monitor.run_monitor", return_value=1)
    def test_lambda_handler_raises_on_monitor_failure(self, _run_monitor) -> None:
        with self.assertRaisesRegex(monitor.MonitorError, "monitor cycle failed"):
            monitor.lambda_handler({}, None)

    @patch("monitor.send_telegram_message")
    @patch("monitor.load_telegram_credentials")
    @patch("monitor.run_monitor")
    def test_lambda_handler_sends_test_notification(
        self,
        run_monitor,
        load_credentials,
        send_message,
    ) -> None:
        load_credentials.return_value = self.credentials

        result = monitor.lambda_handler({"test_notification": True}, None)

        self.assertEqual(result, {"status": "test notification sent"})
        send_message.assert_called_once_with(
            self.credentials,
            monitor.TEST_NOTIFICATION_MESSAGE,
        )
        run_monitor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
