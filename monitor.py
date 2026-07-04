#!/usr/bin/env python3
"""Monitor Meaco Shopify products and send Telegram stock alerts."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REQUEST_TIMEOUT_SECONDS = 15.0
REQUEST_ATTEMPTS = 3
USER_AGENT = "aircon-stock-monitor/1.0"


@dataclass(frozen=True)
class Product:
    """A product page to monitor."""

    name: str
    page_url: str

    @property
    def data_url(self) -> str:
        return f"{self.page_url}.js"


@dataclass(frozen=True)
class ProductStatus:
    """Validated, normalized availability returned by Shopify."""

    title: str
    page_url: str
    price_pence: int
    available: bool


@dataclass(frozen=True)
class TelegramCredentials:
    bot_token: str
    chat_id: str


PRODUCTS: tuple[Product, ...] = (
    Product(
        name="Meaco Cirro 12000 BTU Air Conditioner & Heater",
        page_url=(
            "https://www.meaco.com/products/"
            "meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner-heater"
        ),
    ),
    Product(
        name="Meaco Cirro 12000 BTU Air Conditioner",
        page_url=(
            "https://www.meaco.com/products/"
            "meaco-cirro-12000-btu-super-quiet-smart-portable-air-conditioner"
        ),
    ),
)


class MonitorError(Exception):
    """An expected error safe to display without leaking credentials."""


@contextlib.contextmanager
def _hard_timeout(seconds: float):
    """Bound an operation on Unix even when DNS ignores socket timeouts."""

    if seconds <= 0 or not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        yield
        return

    def raise_timeout(*_args: object) -> None:
        raise TimeoutError()

    previous_handler: Any = None
    try:
        previous_handler = signal.signal(signal.SIGALRM, raise_timeout)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    except (ValueError, OSError):
        if previous_handler is not None:
            signal.signal(signal.SIGALRM, previous_handler)
        yield
        return

    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _read_json_response(
    request: Request,
    *,
    timeout: float,
    opener: Callable[..., Any],
) -> Mapping[str, Any]:
    try:
        with _hard_timeout(timeout):
            with opener(request, timeout=timeout) as response:
                body = response.read()
        payload = json.loads(body.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise MonitorError(f"request failed ({type(exc).__name__})") from exc

    if not isinstance(payload, dict):
        raise MonitorError("response was not a JSON object")
    return payload


def parse_product(product: Product, payload: Mapping[str, Any]) -> ProductStatus:
    """Validate a Shopify payload and normalize its availability."""

    title = payload.get("title")
    price = payload.get("price")
    product_available = payload.get("available")
    variants = payload.get("variants")

    if not isinstance(title, str) or not title.strip():
        raise MonitorError("response is missing a valid title")
    if isinstance(price, bool) or not isinstance(price, int) or price < 0:
        raise MonitorError("response is missing a valid integer price")
    if not isinstance(product_available, bool):
        raise MonitorError("response is missing product availability")
    if not isinstance(variants, list) or not variants:
        raise MonitorError("response is missing product variants")

    variant_availability: list[bool] = []
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict) or not isinstance(variant.get("available"), bool):
            raise MonitorError(f"variant {index + 1} is missing availability")
        variant_availability.append(variant["available"])

    return ProductStatus(
        title=title.strip(),
        page_url=product.page_url,
        price_pence=price,
        available=product_available and any(variant_availability),
    )


def fetch_product(
    product: Product,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    attempts: int = REQUEST_ATTEMPTS,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> ProductStatus:
    """Fetch and validate a product, retrying all expected response failures."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    request = Request(
        product.data_url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    last_error: MonitorError | None = None

    for attempt in range(1, attempts + 1):
        try:
            payload = _read_json_response(request, timeout=timeout, opener=opener)
            return parse_product(product, payload)
        except MonitorError as exc:
            last_error = exc
            if attempt < attempts:
                sleep(float(2 ** (attempt - 1)))

    assert last_error is not None
    raise MonitorError(f"failed after {attempts} attempts: {last_error}") from last_error


def format_gbp(price_pence: int) -> str:
    """Format an integer number of pence without floating-point rounding."""

    if isinstance(price_pence, bool) or not isinstance(price_pence, int) or price_pence < 0:
        raise ValueError("price_pence must be a non-negative integer")
    pounds, pence = divmod(price_pence, 100)
    return f"£{pounds:,}.{pence:02d}"


def build_alert(statuses: Sequence[ProductStatus]) -> str:
    """Build one plain-text Telegram alert for all available products."""

    lines = ["🚨 Meaco stock available!"]
    for status in statuses:
        lines.extend(("", status.title, format_gbp(status.price_pence), status.page_url))
    return "\n".join(lines)


def load_telegram_credentials(
    environment: Mapping[str, str] | None = None,
) -> TelegramCredentials:
    if environment is None:
        environment = os.environ
    token = environment.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = environment.get("TELEGRAM_CHAT_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        raise MonitorError(f"missing required environment variable(s): {', '.join(missing)}")
    return TelegramCredentials(bot_token=token, chat_id=chat_id)


def send_telegram_message(
    credentials: TelegramCredentials,
    message: str,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urlopen,
) -> None:
    """Send a Telegram message while keeping the bot token out of errors."""

    endpoint = f"https://api.telegram.org/bot{credentials.bot_token}/sendMessage"
    body = urlencode({"chat_id": credentials.chat_id, "text": message}).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        payload = _read_json_response(request, timeout=timeout, opener=opener)
    except MonitorError as exc:
        raise MonitorError(f"Telegram delivery failed ({type(exc.__cause__).__name__})") from exc

    if payload.get("ok") is not True:
        description = payload.get("description")
        reason = description if isinstance(description, str) else "unknown API error"
        raise MonitorError(f"Telegram rejected the message: {reason}")


def _safe_error(exc: Exception) -> str:
    return str(exc) if isinstance(exc, MonitorError) else type(exc).__name__


def run_monitor(
    *,
    products: Sequence[Product] = PRODUCTS,
    fetcher: Callable[[Product], ProductStatus] = fetch_product,
    notifier: Callable[[TelegramCredentials, str], None] = send_telegram_message,
    credentials: TelegramCredentials | None = None,
    dry_run: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run one monitoring cycle and return a process-compatible exit code."""

    if not dry_run and credentials is None:
        try:
            credentials = load_telegram_credentials()
        except MonitorError as exc:
            print(f"ERROR: {exc}", file=stderr)
            return 1

    statuses: list[ProductStatus] = []
    errors: list[str] = []

    for product in products:
        try:
            status = fetcher(product)
        except Exception as exc:  # Keep the other product check independent.
            errors.append(f"{product.name}: {_safe_error(exc)}")
            continue
        statuses.append(status)
        label = "IN STOCK" if status.available else "out of stock"
        print(f"{status.title}: {label}", file=stdout)

    available = [status for status in statuses if status.available]
    if available:
        alert = build_alert(available)
        if dry_run:
            print("\nDry-run alert:\n", file=stdout)
            print(alert, file=stdout)
        else:
            assert credentials is not None
            try:
                notifier(credentials, alert)
                print(f"Telegram alert sent for {len(available)} product(s).", file=stdout)
            except Exception as exc:
                errors.append(f"Telegram: {_safe_error(exc)}")
    else:
        print("No monitored products are currently in stock.", file=stdout)

    for error in errors:
        print(f"ERROR: {error}", file=stderr)
    return 1 if errors else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check products and print any alert without contacting Telegram",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_monitor(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
