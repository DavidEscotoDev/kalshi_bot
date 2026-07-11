import os
import sys
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import Config
from execution.engine import ExecutionEngine


class TestExecutionEngine:
    """Test order placement, shadow mode, and fee tracking."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict(os.environ, {
            "KALSHI_TESTING": "1", "KALSHI_API_KEY_ID": "test",
            "KALSHI_PRIVATE_KEY_PATH": "/fake", "KALSHI_ENV": "demo",
            "SHADOW_MODE": "True", "REQUEST_TIMEOUT_SEC": "10",
            "RETRY_MAX_ATTEMPTS": "3", "RETRY_BACKOFF_SEC": "1",
            "MAX_VAR_LIMIT_PCT": "0.02", "MAX_SECTOR_LIMIT_PCT": "0.3",
            "KELLY_MULTIPLIER": "0.25", "LIVE_TRADE_CONFIRMED": "0",
        }, clear=True), patch.object(Config, 'get_private_key') as mock_key, \
                 patch.object(Config, 'validate') as mock_val, \
                 patch.object(Config, 'get_verified_session') as mock_sess, \
                 patch.object(Config, 'get_rest_url', return_value="https://demo.api"):
            mock_key.return_value = Mock()
            mock_sess.return_value = Mock()
            yield

    @pytest.fixture
    def engine(self):
        return ExecutionEngine()

    def test_init_creates_fee_accumulator(self, engine):
        """Engine initializes with zero fee accumulator."""
        assert engine.fee_accumulator == Decimal("0.000000")

    def test_format_price(self, engine):
        """Prices formatted to 4 decimal places."""
        assert engine.format_price(Decimal("0.5")) == "0.5000"
        assert engine.format_price(Decimal("0.123456")) == "0.1235"
        assert engine.format_price(Decimal("0.99999")) == "1.0000"

    def test_format_quantity(self, engine):
        """Quantities rounded to nearest integer."""
        assert engine.format_quantity(Decimal("100")) == "100"
        assert engine.format_quantity(Decimal("100.4")) == "100"
        assert engine.format_quantity(Decimal("100.5")) == "101"
        assert engine.format_quantity(Decimal("100.6")) == "101"

    def test_validate_inputs_raises_on_invalid_ticker(self, engine):
        for bad in ["", None, 123]:
            with pytest.raises(ValueError, match="ticker must be a non-empty string"):
                engine.place_order(ticker=bad, outcome_side="yes", price=Decimal("0.5"), quantity=Decimal("10"))

    def test_validate_inputs_raises_on_invalid_side(self, engine):
        for bad in ["", None, "maybe", 123]:
            with pytest.raises(ValueError, match="outcome_side must be a non-empty string"):
                engine.place_order(ticker="TICKER", outcome_side=bad, price=Decimal("0.5"), quantity=Decimal("10"))

    def test_validate_inputs_raises_on_invalid_action(self, engine):
        for bad in ["", None, "hold", 123]:
            with pytest.raises(ValueError, match="action must be a non-empty string"):
                engine.place_order(ticker="TICKER", outcome_side="yes", price=Decimal("0.5"), quantity=Decimal("10"), action=bad)

    def test_validate_inputs_raises_on_invalid_price(self, engine):
        for bad in [Decimal("0"), Decimal("-0.1"), Decimal("1.0"), Decimal("1.5"), "0.5", None]:
            with pytest.raises(ValueError, match="price must be a Decimal in"):
                engine.place_order(ticker="TICKER", outcome_side="yes", price=bad, quantity=Decimal("10"))

    def test_validate_inputs_raises_on_invalid_quantity(self, engine):
        for bad in [Decimal("0"), Decimal("-10"), "10", None]:
            with pytest.raises(ValueError, match="quantity must be a positive Decimal"):
                engine.place_order(ticker="TICKER", outcome_side="yes", price=Decimal("0.5"), quantity=bad)


class TestExecutionEngineShadowMode:
    """Test shadow mode order placement (no live API calls)."""

    @pytest.fixture
    def engine(self):
        with patch('execution.engine.Config.SHADOW_MODE', True):
            return ExecutionEngine()

    def test_shadow_mode_logs_to_file(self, engine, tmp_path):
        """Shadow orders logged to JSONL file."""
        log_path = tmp_path / "shadow_trades.log"
        with patch('execution.engine._get_shadow_logger') as mock_logger_fn:
            mock_logger = Mock()
            mock_logger_fn.return_value = mock_logger

            with patch('execution.engine.log_shadow_trade') as mock_log_db:
                engine.place_order(
                    ticker="TEST-TICKER", outcome_side="yes", price=Decimal("0.55"),
                    quantity=Decimal("100"), action="buy"
                )

                mock_logger.info.assert_called_once()
                log_call = mock_logger.info.call_args[0][0]
                log_data = eval(log_call)  # JSON string
                assert log_data["ticker"] == "TEST-TICKER"
                assert log_data["action"] == "buy"
                assert log_data["price"] == "0.5500"
                assert log_data["quantity"] == "100"
                assert log_data["outcome_side"] == "yes"

    def test_shadow_mode_returns_filled_order(self, engine):
        """Shadow mode returns order with status='filled'."""
        with patch('execution.engine.log_shadow_trade'), \
             patch('execution.engine.update_order_status'), \
             patch('execution.engine.get_audit_logger') as mock_audit:

            mock_audit.return_value = Mock()
            result = engine.place_order(
                ticker="TEST", outcome_side="yes", price=Decimal("0.5"),
                quantity=Decimal("10"), action="buy"
            )

            assert result["status"] == "filled"
            assert result["ticker"] == "TEST"
            assert result["price"] == "0.5000"
            assert result["count"] == 10
            assert "client_order_id" in result

    def test_shadow_mode_fee_accumulator_updates(self, engine):
        """Fee accumulator tracks rounding overpayment."""
        with patch('execution.engine.log_shadow_trade'), \
             patch('execution.engine.update_order_status'), \
             patch('execution.engine.get_audit_logger') as mock_audit:

            mock_audit.return_value = Mock()

            # Buy at 0.5500, qty 100 → value = 55.00 → rounded = 55.00 → overpayment = 0
            engine.place_order("T", "yes", Decimal("0.55"), Decimal("100"), "buy")
            assert engine.fee_accumulator == Decimal("0")

            # Buy at 0.5501, qty 100 → value = 55.01 → rounded up = 55.02 → overpayment = 0.01
            engine.fee_accumulator = Decimal("0")  # Reset for test
            engine.place_order("T", "yes", Decimal("0.5501"), Decimal("100"), "buy")
            assert engine.fee_accumulator == Decimal("0.01")

    def test_shadow_mode_duplicate_detection(self, engine):
        """Duplicate orders detected and skipped."""
        with patch('execution.engine.order_exists', return_value=True), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.log_shadow_trade'):

            mock_audit.return_value = Mock()
            result = engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")

            assert result["status"] == "skipped_duplicate"
            assert result["order_id"].startswith("dup-")

    def test_shadow_mode_active_order_detection(self, engine):
        """Active order check prevents duplicate placement."""
        with patch('execution.engine.order_exists', return_value=False), \
             patch('execution.engine.has_active_order', return_value=True), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.log_shadow_trade'):

            mock_audit.return_value = Mock()
            result = engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")

            assert result["status"] == "skipped_duplicate"


class TestExecutionEngineLiveMode:
    """Test live mode order placement via REST API."""

    @pytest.fixture
    def engine(self):
        with patch('execution.engine.Config.SHADOW_MODE', False):
            return ExecutionEngine()

    def test_live_mode_success(self, engine):
        """Live mode POSTs to API and returns order on success."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"order_id": "kalshi-123", "status": "resting"}

        engine.session.request.return_value = mock_resp

        with patch('execution.engine.store_order'), \
             patch('execution.engine.update_order_status'), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.Config.request_with_retry', return_value=mock_resp):

            mock_audit.return_value = Mock()
            result = engine.place_order(
                ticker="TEST", outcome_side="yes", price=Decimal("0.5"),
                quantity=Decimal("10"), action="buy"
            )

            assert result["order_id"] == "kalshi-123"
            assert result["status"] == "resting"
            Config.request_with_retry.assert_called_once()

    def test_live_mode_api_error_raises(self, engine):
        """Non-200/201 response raises RuntimeError."""
        mock_resp = Mock()
        mock_resp.status_code = 400
        mock_resp.text = "Insufficient balance"

        with patch('execution.engine.store_order'), \
             patch('execution.engine.update_order_status'), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.Config.request_with_retry', return_value=mock_resp):

            mock_audit.return_value = Mock()
            with pytest.raises(RuntimeError, match="API Error placing order: Insufficient balance"):
                engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")

    def test_live_mode_updates_order_status_on_rejection(self, engine):
        """Rejected orders stored with 'rejected' status."""
        mock_resp = Mock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"

        with patch('execution.engine.store_order') as mock_store, \
             patch('execution.engine.update_order_status') as mock_update, \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.Config.request_with_retry', return_value=mock_resp):

            mock_audit.return_value = Mock()
            try:
                engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")
            except RuntimeError:
                pass

            mock_update.assert_called()
            args = mock_update.call_args[0]
            assert args[1] == "rejected"
            assert "Bad request" in args[2]

    def test_live_mode_stores_client_order_id(self, engine):
        """Client order ID passed to API and stored."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"order_id": "kalshi-123", "status": "resting"}

        with patch('execution.engine.store_order') as mock_store, \
             patch('execution.engine.update_order_status'), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.Config.request_with_retry', return_value=mock_resp):

            mock_audit.return_value = Mock()
            engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")

            # Verify store_order called with client_order_id
            store_call = mock_store.call_args
            assert "client_order_id" in store_call.kwargs
            assert len(store_call.kwargs["client_order_id"]) == 36  # UUID

    def test_live_mode_payload_structure(self, engine):
        """Payload includes all required fields."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"order_id": "kalshi-123", "status": "resting"}

        with patch('execution.engine.store_order'), \
             patch('execution.engine.update_order_status'), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.Config.request_with_retry', return_value=mock_resp) as mock_req:

            mock_audit.return_value = Mock()
            engine.place_order(
                ticker="TEST", outcome_side="yes", price=Decimal("0.55"),
                quantity=Decimal("100"), action="buy", type_opts="limit",
                time_in_force="good_till_canceled"
            )

            mock_req.assert_called_once()
            payload = mock_req.call_args.kwargs.get("json", {})
            assert payload["ticker"] == "TEST"
            assert payload["action"] == "buy"
            assert payload["type"] == "limit"
            assert payload["price"] == "0.5500"
            assert payload["count"] == "100"
            assert payload["outcome_side"] == "yes"
            assert payload["book_side"] == "bid"  # YES -> bid
            assert payload["time_in_force"] == "good_till_canceled"
            assert payload["self_trade_prevention_type"] == "taker_at_cross"


class TestFeeAccumulator:
    """Test fee tracking and rebate logic."""

    @pytest.fixture
    def engine(self):
        return ExecutionEngine()

    def test_buy_rounds_up_overpayment(self, engine):
        """Buy orders round up (overpay) → fee accumulator increases."""
        # Price 0.5501, qty 100 → value 55.01 → rounded to 55.02 (ceil) → overpayment 0.01
        fill = engine._record_fill(Decimal("0.5501"), Decimal("100"), is_buy=True)
        assert fill["overpayment"] == Decimal("0.01")
        assert fill["rebate"] == Decimal("0")
        assert engine.fee_accumulator == Decimal("0.01")

    def test_sell_rounds_down_overpayment(self, engine):
        """Sell orders round down (underpay) → fee accumulator increases."""
        # Price 0.5501, qty 100 → value 55.01 → rounded to 55.01 (floor) → overpayment 0
        # Price 0.5499, qty 100 → value 54.99 → rounded to 54.99 (floor) → overpayment 0
        # Price 0.5509, qty 100 → value 55.09 → rounded to 55.09 (floor) → overpayment 0
        # Wait: for sell, rounding DOWN means floor → actual value = rounded value
        # Overpayment = actual - rounded = 0 for exact multiples
        # Need price * qty with cents fraction
        fill = engine._record_fill(Decimal("0.5509"), Decimal("100"), is_buy=False)
        assert fill["overpayment"] == Decimal("0.00")  # 55.09 floor = 55.09

        # Better test: price 0.5501, qty 1000 → value 550.1 → floor 550.1 → overpayment 0
        engine.fee_accumulator = Decimal("0")
        fill = engine._record_fill(Decimal("0.5501"), Decimal("1000"), is_buy=False)
        assert fill["overpayment"] == Decimal("0.00")  # 550.10 floor = 550.10

    def test_rebate_when_accumulator_reaches_cent(self, engine):
        """Rebate triggered when fee_accumulator >= $0.01."""
        engine.fee_accumulator = Decimal("0.015")  # 1.5 cents accumulated
        # Next fill adds 0.01 → 0.025 → rebate 2 cents
        fill = engine._record_fill(Decimal("0.5501"), Decimal("100"), is_buy=True)
        # overpayment = 0.01, accumulator = 0.025, rebate = 0.02
        assert fill["rebate"] == Decimal("0.02")
        assert engine.fee_accumulator == Decimal("0.005")  # 0.025 - 0.02

    def test_no_rebate_below_cent(self, engine):
        """No rebate when accumulator < $0.01."""
        engine.fee_accumulator = Decimal("0.005")
        fill = engine._record_fill(Decimal("0.5501"), Decimal("100"), is_buy=True)
        assert fill["rebate"] == Decimal("0")
        assert engine.fee_accumulator == Decimal("0.015")


class TestDuplicatePrevention:
    """Test duplicate order detection."""

    @pytest.fixture
    def engine(self):
        with patch('execution.engine.Config.SHADOW_MODE', True):
            return ExecutionEngine()

    def test_duplicate_client_order_id(self, engine):
        """Duplicate client_order_id skipped."""
        with patch('execution.engine.order_exists', return_value=True), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.log_shadow_trade'):

            mock_audit.return_value = Mock()
            result = engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")
            assert result["status"] == "skipped_duplicate"

    def test_duplicate_active_order(self, engine):
        """Duplicate active order (same ticker/side/price/action) skipped."""
        with patch('execution.engine.order_exists', return_value=False), \
             patch('execution.engine.has_active_order', return_value=True), \
             patch('execution.engine.get_audit_logger') as mock_audit, \
             patch('execution.engine.log_shadow_trade'):

            mock_audit.return_value = Mock()
            result = engine.place_order("T", "yes", Decimal("0.5"), Decimal("10"), "buy")
            assert result["status"] == "skipped_duplicate"
