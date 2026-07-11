import os
import sys
import time
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import Config
from safety.kill_switch import KillSwitch


def _make_private_key_mock():
    """Create a proper mock for RSA private key with sign() returning bytes."""
    mock_key = Mock()
    mock_signature = Mock()
    mock_signature.__bytes__ = Mock(return_value=b"fake_signature_bytes")
    mock_key.sign.return_value = mock_signature
    return mock_key


class TestKillSwitch:
    """Test kill switch balance monitoring and emergency cancellation."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict(os.environ, {
            "KALSHI_TESTING": "1", "KALSHI_API_KEY_ID": "test_key",
            "KALSHI_PRIVATE_KEY_PATH": "/fake/key", "KALSHI_ENV": "demo",
            "REQUEST_TIMEOUT_SEC": "10", "RETRY_MAX_ATTEMPTS": "3",
            "RETRY_BACKOFF_SEC": "1", "MAX_VAR_LIMIT_PCT": "0.02",
            "MAX_SECTOR_LIMIT_PCT": "0.3", "KELLY_MULTIPLIER": "0.25",
            "KILL_SWITCH_MIN_BALANCE": "100",
        }, clear=True), patch.object(Config, 'get_private_key') as mock_key, \
                 patch.object(Config, 'validate') as mock_val, \
                 patch.object(Config, 'get_verified_session') as mock_sess, \
                 patch.object(Config, 'get_rest_url', return_value="https://demo.api"):
            mock_key.return_value = _make_private_key_mock()
            mock_sess.return_value = Mock()
            yield

    @pytest.fixture
    def kill_switch(self, patch_config):
        """Create KillSwitch with mocked Config.request_with_retry."""
        mock_req = patch_config
        ks = KillSwitch()
        ks.sign_headers = Mock(return_value={"Authorization": "test"})
        return ks

    def test_init_validates_config(self):
        """Constructor calls Config.validate()."""
        with patch.object(Config, 'validate') as mock_val:
            KillSwitch()
            mock_val.assert_called_once()

    def test_get_balance_success(self, kill_switch, patch_config):
        """get_balance() returns balance from API response."""
        mock_req = patch_config
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"balance_dollars": "1234.56"}
        mock_req.return_value = mock_resp

        balance = kill_switch.get_balance()
        assert balance == Decimal("1234.56")
        mock_req.assert_called_once()

    def test_get_balance_api_error_raises(self, kill_switch, patch_config):
        """Non-200 response raises RuntimeError."""
        mock_req = patch_config
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_req.return_value = mock_resp

        with pytest.raises(RuntimeError, match="API Error fetching balance"):
            kill_switch.get_balance()

    def test_get_balance_missing_field_raises(self, kill_switch, patch_config):
        """Missing balance_dollars in response raises ValueError."""
        mock_req = patch_config
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"other_field": "value"}
        mock_req.return_value = mock_resp

        with pytest.raises(ValueError, match="missing 'balance_dollars'"):
            kill_switch.get_balance()

    def test_cancel_all_orders_success(self, kill_switch, patch_config):
        """cancel_all_orders fetches and cancels all resting orders."""
        mock_req = patch_config
        list_resp = Mock()
        list_resp.status_code = 200
        list_resp.json.return_value = {
            "orders": [
                {"order_id": "order1"},
                {"order_id": "order2"},
                {"order_id": "order3"},
            ]
        }

        cancel_resp = Mock()
        cancel_resp.status_code = 200

        mock_req.side_effect = [list_resp, cancel_resp, cancel_resp, cancel_resp]

        cancelled = kill_switch.cancel_all_orders()
        assert cancelled == 3
        assert mock_req.call_count == 4

    def test_cancel_all_orders_partial_failure(self, kill_switch, patch_config):
        """Continues cancelling other orders if one fails."""
        mock_req = patch_config
        list_resp = Mock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"orders": [{"order_id": "ok"}, {"order_id": "fail"}]}

        ok_resp = Mock()
        ok_resp.status_code = 200
        fail_resp = Mock()
        fail_resp.status_code = 500
        fail_resp.text = "Server error"

        mock_req.side_effect = [list_resp, ok_resp, fail_resp]

        with patch('safety.kill_switch.logger.error') as mock_err:
            cancelled = kill_switch.cancel_all_orders()
            assert cancelled == 1
            mock_err.assert_called_once()

    def test_cancel_all_orders_no_resting_orders(self, kill_switch, patch_config):
        """Returns 0 if no resting orders."""
        mock_req = patch_config
        list_resp = Mock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"orders": []}
        mock_req.return_value = list_resp

        cancelled = kill_switch.cancel_all_orders()
        assert cancelled == 0

    def test_cancel_all_orders_list_failure_raises(self, kill_switch, patch_config):
        """Failure to list orders raises RuntimeError."""
        mock_req = patch_config
        list_resp = Mock()
        list_resp.status_code = 500
        list_resp.text = "Error"
        mock_req.return_value = list_resp

        with pytest.raises(RuntimeError, match="API Error listing orders"):
            kill_switch.cancel_all_orders()


class TestKillSwitchTrigger:
    """Test kill switch trigger logic."""

    @pytest.fixture
    def kill_switch(self):
        with patch.object(KillSwitch, 'get_balance') as mock_balance:
            mock_balance.return_value = Decimal("1000")
            ks = KillSwitch()
            ks.get_balance = mock_balance
            ks.cancel_all_orders = Mock(return_value=5)
            return ks

    def test_check_and_trigger_balance_above_threshold(self, kill_switch):
        """Returns (False, balance) when balance > min."""
        kill_switch.get_balance.return_value = Decimal("500")
        with patch.object(Config, 'KILL_SWITCH_MIN_BALANCE', Decimal("100")):
            triggered, balance = kill_switch.check_and_trigger_with_capital()
            assert triggered is False
            assert balance == Decimal("500")

    def test_check_and_trigger_balance_below_threshold(self, kill_switch):
        """Triggers and cancels orders when balance < min."""
        kill_switch.get_balance.return_value = Decimal("50")
        kill_switch.cancel_all_orders = Mock(return_value=5)

        with patch.object(Config, 'KILL_SWITCH_MIN_BALANCE', Decimal("100")):
            triggered, balance = kill_switch.check_and_trigger_with_capital()
            assert triggered is True
            assert balance == Decimal("50")
            kill_switch.cancel_all_orders.assert_called_once()

    def test_check_and_trigger_balance_api_failure_triggers(self, kill_switch):
        """API failure triggers fail-closed behavior."""
        kill_switch.get_balance.side_effect = Exception("Network error")
        kill_switch.cancel_all_orders = Mock(return_value=3)

        with patch.object(Config, 'KILL_SWITCH_MIN_BALANCE', Decimal("100")):
            with patch('safety.kill_switch.logger.critical') as mock_crit:
                triggered, balance = kill_switch.check_and_trigger_with_capital()
                assert triggered is True
                assert balance == Decimal("0")
                kill_switch.cancel_all_orders.assert_called_once()
                mock_crit.assert_called_once()
                assert "FORCING ORDER CANCELLATION" in mock_crit.call_args[0][0]

    def test_check_and_trigger_zero_balance_triggers(self, kill_switch):
        """Zero balance triggers kill switch."""
        kill_switch.get_balance.return_value = Decimal("0")
        kill_switch.cancel_all_orders = Mock()

        with patch.object(Config, 'KILL_SWITCH_MIN_BALANCE', Decimal("100")):
            triggered, _ = kill_switch.check_and_trigger_with_capital()
            assert triggered is True

    def test_balance_caching(self, kill_switch):
        """Balance cached for _BALANCE_CACHE_TTL (5 seconds)."""
        kill_switch.get_balance.return_value = Decimal("1000")

        bal1 = kill_switch.get_cached_balance()
        bal2 = kill_switch.get_cached_balance()

        assert bal1 == bal2 == Decimal("1000")
        assert kill_switch.get_balance.call_count == 1

    def test_balance_cache_expires(self, kill_switch):
        """Cache expires after _BALANCE_CACHE_TTL."""
        kill_switch.get_balance.side_effect = [Decimal("1000"), Decimal("2000")]

        bal1 = kill_switch.get_cached_balance()
        kill_switch._last_check_time = time.time() - 10
        bal2 = kill_switch.get_cached_balance()

        assert bal1 == Decimal("1000")
        assert bal2 == Decimal("2000")
        assert kill_switch.get_balance.call_count == 2


class TestKillSwitchPositions:
    """Test position retrieval."""

    @pytest.fixture
    def kill_switch(self):
        ks = KillSwitch()
        ks.sign_headers = Mock(return_value={"auth": "header"})
        return ks

    def test_get_positions_success(self, kill_switch, patch_config):
        """Returns positions list from API."""
        mock_req = patch_config
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "positions": [
                {"ticker": "T1", "side": "yes", "quantity": "10"},
                {"ticker": "T2", "side": "no", "quantity": "5"},
            ]
        }
        mock_req.return_value = mock_resp

        positions = kill_switch.get_positions()
        assert len(positions) == 2
        assert positions[0]["ticker"] == "T1"

    def test_get_positions_api_error_returns_empty(self, kill_switch, patch_config):
        """Returns empty list on API error."""
        mock_req = patch_config
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Error"
        mock_req.return_value = mock_resp

        with patch('safety.kill_switch.logger.error'):
            positions = kill_switch.get_positions()
            assert positions == []

    def test_check_and_trigger_delegates(self, kill_switch):
        """check_and_trigger delegates to check_and_trigger_with_capital."""
        kill_switch.check_and_trigger_with_capital = Mock(return_value=(True, Decimal("50")))
        result = kill_switch.check_and_trigger()
        assert result is True
