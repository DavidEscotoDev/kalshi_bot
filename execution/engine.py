import json
import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from cryptography.hazmat.primitives.asymmetric import rsa

from config import Config, sign_kalshi_headers
from data.audit_log import get_audit_logger
from data.database import (
    has_active_order,
    log_shadow_trade,
    order_exists,
    store_order,
    update_order_status,
)

_SHADOW_LOGGER: logging.Logger | None = None


def _get_shadow_logger() -> logging.Logger:
    global _SHADOW_LOGGER
    if _SHADOW_LOGGER is not None:
        return _SHADOW_LOGGER
    from logging.handlers import RotatingFileHandler

    shadow_log_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "logs", "shadow_trades.log")
    )
    os.makedirs(os.path.dirname(shadow_log_path), exist_ok=True)
    _SHADOW_LOGGER = logging.getLogger("kalshi_bot.shadow_trades")
    _SHADOW_LOGGER.setLevel(logging.INFO)
    _SHADOW_LOGGER.propagate = False
    handler = RotatingFileHandler(shadow_log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _SHADOW_LOGGER.addHandler(handler)
    return _SHADOW_LOGGER


logger = logging.getLogger("kalshi_bot.execution")


class ExecutionEngine:
    def __init__(self) -> None:
        Config.validate()
        self.api_key_id = Config.API_KEY_ID
        self.private_key: rsa.RSAPrivateKey | None = None
        self.base_url = Config.get_rest_url()

        # ponytail: inline fee accumulator instead of separate FeeAccumulatorTracker
        self.fee_accumulator = Decimal("0.000000")

    def format_price(self, price: Decimal) -> str:
        return f"{price:.4f}"

    def format_quantity(self, quantity: Decimal) -> str:
        return str(int(quantity.to_integral_value(rounding="ROUND_HALF_UP")))

    def sign_headers(self, method: str, path: str) -> dict[str, str]:
        if not self.private_key:
            self.private_key = Config.get_private_key()
        return sign_kalshi_headers(self.api_key_id, self.private_key, method, path)  # type: ignore[arg-type]

    def _record_fill(self, price: Decimal, quantity: Decimal, is_buy: bool = True) -> dict:
        actual_value = price * quantity
        actual_value_cents = actual_value * Decimal("100.0")

        if is_buy:
            rounded_cents = actual_value_cents.quantize(Decimal("1"), rounding=ROUND_CEILING)
            rounded_value = rounded_cents / Decimal("100.0")
            overpayment = rounded_value - actual_value
        else:
            rounded_cents = actual_value_cents.quantize(Decimal("1"), rounding=ROUND_FLOOR)
            rounded_value = rounded_cents / Decimal("100.0")
            overpayment = actual_value - rounded_value

        self.fee_accumulator += overpayment

        rebate = Decimal("0.00")
        if self.fee_accumulator >= Decimal("0.01"):
            cents = int(self.fee_accumulator // Decimal("0.01"))
            rebate = Decimal(cents) * Decimal("0.01")
            self.fee_accumulator -= rebate

        return {
            "actual_value": actual_value,
            "rounded_value": rounded_value,
            "overpayment": overpayment,
            "rebate": rebate,
        }

    def place_order(
        self,
        ticker: str,
        outcome_side: str,
        price: Decimal,
        quantity: Decimal,
        action: str = "buy",
        type_opts: str = "limit",
        time_in_force: str = "good_till_canceled",
        synthetic_ask: Decimal | None = None,
        release_id: int | None = None,
        proposed_kelly: Decimal | None = None,
        final_wager: Decimal | None = None,
        signal_id: int | None = None,
    ) -> dict:
        if not ticker or not isinstance(ticker, str):
            raise ValueError(f"ticker must be a non-empty string, got: {ticker!r}")
        if not outcome_side or not isinstance(outcome_side, str):
            raise ValueError(f"outcome_side must be a non-empty string, got: {outcome_side!r}")
        if not action or not isinstance(action, str):
            raise ValueError(f"action must be a non-empty string, got: {action!r}")
        if not isinstance(price, Decimal) or price <= Decimal("0") or price >= Decimal("1.0"):
            raise ValueError(f"price must be a Decimal in (0, 1.0), got: {price!r}")
        if not isinstance(quantity, Decimal) or quantity <= Decimal("0"):
            raise ValueError(f"quantity must be a positive Decimal, got: {quantity!r}")

        price_str = self.format_price(price)
        price = Decimal(price_str)
        qty_int = int(quantity.to_integral_value(rounding="ROUND_HALF_UP"))
        qty_str = str(qty_int)
        client_order_id = str(uuid.uuid4())
        _audit = get_audit_logger()

        outcome_side_lower = outcome_side.strip().lower()
        action_lower = action.strip().lower()

        if outcome_side_lower not in ("yes", "no"):
            raise ValueError(f"Invalid outcome_side: {outcome_side}. Must be 'yes' or 'no'.")
        if action_lower not in ("buy", "sell"):
            raise ValueError(f"Invalid action: {action}. Must be 'buy' or 'sell'.")

        book_side = "bid" if outcome_side_lower == "yes" else "ask"

        if order_exists(client_order_id) or has_active_order(
            ticker, outcome_side_lower, float(price), action_lower
        ):
            logger.warning(
                f"Duplicate order detected for {ticker} {action_lower} {outcome_side_lower} "
                f"@{price_str}. Skipping to prevent double execution."
            )
            _audit.log(
                "order_duplicate_skipped",
                ticker,
                action_lower,
                outcome_side=outcome_side_lower,
                client_order_id=client_order_id,
            )
            return {
                "order_id": f"dup-{client_order_id}",
                "ticker": ticker,
                "status": "skipped_duplicate",
                "client_order_id": client_order_id,
            }

        store_order(
            client_order_id=client_order_id,
            ticker=ticker,
            status="pending",
            action=action_lower,
            outcome_side=outcome_side_lower,
            price=float(price),
            quantity=float(quantity),
            signal_id=signal_id,
        )

        payload = {
            "ticker": ticker,
            "action": action_lower,
            "type": type_opts,
            "price": price_str,
            "count": qty_str,
            "outcome_side": outcome_side_lower,
            "book_side": book_side,
            "client_order_id": client_order_id,
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
        }

        if Config.SHADOW_MODE:
            self._record_fill(price, quantity, is_buy=(action_lower == "buy"))

            _get_shadow_logger().info(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                        "ticker": ticker,
                        "action": action_lower,
                        "price": price_str,
                        "quantity": qty_str,
                        "outcome_side": outcome_side_lower,
                        "synthetic_ask": (
                            float(synthetic_ask) if synthetic_ask is not None else None
                        ),
                        "fee_accumulator": float(self.fee_accumulator),
                    }
                )
            )

            log_shadow_trade(
                ticker=ticker,
                timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
                action=action_lower,
                outcome_side=outcome_side_lower,
                price=float(price),
                quantity=float(quantity),
                synthetic_ask=float(synthetic_ask) if synthetic_ask is not None else None,
                proposed_kelly=float(proposed_kelly) if proposed_kelly is not None else None,
                final_wager=float(final_wager) if final_wager is not None else None,
                fee_accumulator=float(self.fee_accumulator),
                release_id=release_id,
            )

            update_order_status(client_order_id, "filled")
            _audit.log_order_placed(
                ticker,
                action_lower,
                outcome_side_lower,
                price=float(price),
                quantity=float(quantity),
                client_order_id=client_order_id,
            )

            logger.info(
                f"[SHADOW MODE] Intercepted order for {ticker}. "
                "Payload logged to shadow_trades.log & SQLite."
            )

            return {
                "order_id": f"shadow-{uuid.uuid4()}",
                "ticker": ticker,
                "action": action_lower,
                "type": type_opts,
                "price": price_str,
                "count": qty_int,
                "outcome_side": outcome_side_lower,
                "book_side": book_side,
                "status": "filled",
                "client_order_id": client_order_id,
            }

        path = f"/trade-api/{Config.API_VERSION}/portfolio/orders"
        url = f"{self.base_url}{path}"
        headers = self.sign_headers("POST", path)

        logger.info(
            f"Placing live {action_lower} order: outcome={outcome_side_lower} ({book_side}) "
            f"qty={qty_str} @ price={price_str} for {ticker}"
        )

        session = Config.get_verified_session()
        response = Config.request_with_retry(
            method="POST",
            url=url,
            json=payload,
            headers=headers,
            session=session,
            timeout=Config.REQUEST_TIMEOUT_SEC,
        )

        if response.status_code not in (200, 201):
            error_msg = response.text
            update_order_status(client_order_id, "rejected", error_message=error_msg)
            _audit.log_order_rejected(
                ticker,
                action_lower,
                outcome_side_lower,
                reason=error_msg,
                client_order_id=client_order_id,
            )
            logger.error(f"Failed to place order: {error_msg}")
            raise RuntimeError(f"API Error placing order: {error_msg}")

        order_data = response.json()
        kalshi_order_id = order_data.get("order_id")
        api_status = order_data.get("status", "submitted")

        update_order_status(client_order_id, api_status, kalshi_order_id=kalshi_order_id)
        _audit.log_order_placed(
            ticker,
            action_lower,
            outcome_side_lower,
            price=float(price),
            quantity=float(quantity),
            order_id=kalshi_order_id,
            client_order_id=client_order_id,
        )

        logger.info(f"Order successfully placed: {kalshi_order_id}")
        return order_data
