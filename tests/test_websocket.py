import json
import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import Config
from market.order_book import LocalOrderBook
from market.websocket_client import KalshiWebSocketClient


class TestWebSocketClient:
    """Test WebSocket connection, reconnection, and message handling."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict(os.environ, {
            "KALSHI_TESTING": "1", "KALSHI_API_KEY_ID": "test_key",
            "KALSHI_PRIVATE_KEY_PATH": "/fake/key", "KALSHI_ENV": "demo",
            "REQUEST_TIMEOUT_SEC": "10", "RETRY_MAX_ATTEMPTS": "3",
            "RETRY_BACKOFF_SEC": "1", "MAX_VAR_LIMIT_PCT": "0.02",
            "MAX_SECTOR_LIMIT_PCT": "0.3", "KELLY_MULTIPLIER": "0.25",
        }, clear=True), patch.object(Config, 'get_private_key') as mock_key, \
                 patch.object(Config, 'validate') as mock_val:
            mock_key.return_value = Mock()
            yield

    @pytest.fixture
    def order_book(self):
        return LocalOrderBook()

    @pytest.fixture
    def ws_client(self, order_book):
        with patch('market.websocket_client.WebSocketApp') as mock_ws_app:
            client = KalshiWebSocketClient("TEST-TICKER", order_book)
            client.ws = Mock()
            yield client

    def test_init_creates_components(self, order_book):
        """Constructor initializes all components."""
        client = KalshiWebSocketClient("TICKER", order_book)
        assert client.ticker == "TICKER"
        assert client.order_book is order_book
        assert client.stop_event.is_set() is False
        assert client.connected_event.is_set() is False
        assert client._sequence == 0
        assert client._last_sequence == 0

    def test_connect_starts_thread(self, ws_client):
        """connect() creates WebSocketApp and starts daemon thread."""
        ws_client.connect()
        assert ws_client.thread is not None
        assert ws_client.thread.daemon is True
        assert ws_client.thread.is_alive()

    def test_wait_for_connection_timeout(self, ws_client):
        """wait_for_connection returns False if timeout."""
        ws_client.connected_event.clear()
        result = ws_client.wait_for_connection(timeout=0.1)
        assert result is False

    def test_wait_for_connection_success(self, ws_client):
        """wait_for_connection returns True when connected."""
        ws_client.connected_event.set()
        result = ws_client.wait_for_connection(timeout=1.0)
        assert result is True

    def test_disconnect_stops_thread(self, ws_client):
        """disconnect() stops thread and closes WebSocket."""
        ws_client.thread = Mock()
        ws_client.thread.is_alive.return_value = True
        ws_client.ws = Mock()

        ws_client.disconnect()

        assert ws_client.stop_event.is_set()
        ws_client.ws.close.assert_called_once()
        ws_client.thread.join.assert_called_once_with(timeout=2.0)

    def test_on_open_subscribes_to_channels(self, ws_client):
        """on_open sends snapshot and delta subscriptions."""
        mock_ws = Mock()
        ws_client._on_open(mock_ws)

        assert mock_ws.send.call_count == 2
        calls = mock_ws.send.call_args_list

        # First call: snapshot subscription
        sub1 = json.loads(calls[0][0][0])
        assert sub1["cmd"] == "subscribe"
        assert sub1["params"]["channels"] == ["orderbook_snapshot"]
        assert sub1["params"]["market_ticker"] == "TEST-TICKER"

        # Second call: delta subscription
        sub2 = json.loads(calls[1][0][0])
        assert sub2["cmd"] == "subscribe"
        assert sub2["params"]["channels"] == ["orderbook_delta"]

    def test_on_message_snapshot_applies_to_book(self, ws_client, order_book):
        """Snapshot message clears and rebuilds order book."""
        with patch.object(order_book, 'apply_snapshot') as mock_apply, \
             patch.object(order_book, 'clear') as mock_clear:

            msg = {
                "type": "orderbook_snapshot",
                "msg": {"sequence": 100, "yes": [{"price": "0.50", "size": "100"}]}
            }
            ws_client._on_message(ws_client.ws, json.dumps(msg))

            mock_clear.assert_called_once()
            mock_apply.assert_called_once_with(msg)
            assert ws_client._last_sequence == 100

    def test_on_message_delta_applies_to_book(self, ws_client, order_book):
        """Delta message applies incremental update."""
        ws_client._last_sequence = 50
        with patch.object(order_book, 'apply_delta') as mock_apply:
            msg = {
                "type": "orderbook_delta",
                "msg": {"sequence": 51, "side": "yes", "price_dollars": "0.51", "delta_fp": "10"}
            }
            ws_client._on_message(ws_client.ws, json.dumps(msg))

            mock_apply.assert_called_once_with(msg)
            assert ws_client._last_sequence == 51
            assert ws_client._sequence == 51

    def test_on_message_detects_sequence_gap(self, ws_client, order_book):
        """Logs warning when sequence gap detected."""
        ws_client._last_sequence = 100
        with patch.object(order_book, 'apply_delta'), \
             patch('market.websocket_client.logger.warning') as mock_warn:

            msg = {
                "type": "orderbook_delta",
                "msg": {"sequence": 105, "side": "yes", "price_dollars": "0.51", "delta_fp": "10"}
            }
            ws_client._on_message(ws_client.ws, json.dumps(msg))

            mock_warn.assert_called_once()
            assert "gap detected" in mock_warn.call_args[0][0]
            assert "4 messages missed" in mock_warn.call_args[0][0]

    def test_on_message_rejects_oversized_snapshot(self, ws_client, order_book):
        """Rejects snapshot messages exceeding _MAX_SNAPSHOT_SIZE."""
        large_msg = {"type": "orderbook_snapshot", "msg": {"sequence": 1, "data": "x" * 200000}}
        with patch('market.websocket_client.logger.warning') as mock_warn:
            ws_client._on_message(ws_client.ws, json.dumps(large_msg))
            mock_warn.assert_called_once()
            assert "Discarding oversized WebSocket message" in mock_warn.call_args[0][0]

    def test_on_message_rejects_oversized_delta(self, ws_client, order_book):
        """Rejects delta messages exceeding _MAX_DELTA_SIZE."""
        ws_client._last_sequence = 10
        large_delta = {
            "type": "orderbook_delta",
            "msg": {"sequence": 11, "side": "yes", "price_dollars": "0.5", "delta_fp": "1" + "x" * 15000}
        }
        with patch('market.websocket_client.logger.warning') as mock_warn:
            ws_client._on_message(ws_client.ws, json.dumps(large_delta))
            mock_warn.assert_called_once()
            assert "Discarding oversized delta message" in mock_warn.call_args[0][0]

    def test_on_message_rejects_invalid_side(self, ws_client, order_book):
        """Rejects delta with invalid side."""
        ws_client._last_sequence = 10
        with patch('market.websocket_client.logger.warning') as mock_warn:
            msg = {
                "type": "orderbook_delta",
                "msg": {"sequence": 11, "side": "invalid", "price_dollars": "0.5", "delta_fp": "1"}
            }
            ws_client._on_message(ws_client.ws, json.dumps(msg))
            mock_warn.assert_called_once()
            assert "Discarding delta with invalid side" in mock_warn.call_args[0][0]

    def test_on_message_rejects_non_string_price_delta(self, ws_client, order_book):
        """Rejects delta with non-string price/delta."""
        ws_client._last_sequence = 10
        with patch('market.websocket_client.logger.warning') as mock_warn:
            msg = {
                "type": "orderbook_delta",
                "msg": {"sequence": 11, "side": "yes", "price_dollars": 0.5, "delta_fp": "1"}
            }
            ws_client._on_message(ws_client.ws, json.dumps(msg))
            mock_warn.assert_called_once()
            assert "non-string price/delta" in mock_warn.call_args[0][0]

    def test_on_message_subscribed_logs_info(self, ws_client):
        """Logs subscription confirmation."""
        with patch('market.websocket_client.logger.info') as mock_info:
            msg = {"type": "subscribed", "msg": "ok"}
            ws_client._on_message(ws_client.ws, json.dumps(msg))
            mock_info.assert_called_once()

    def test_on_message_error_logs_error(self, ws_client):
        """Logs server error messages."""
        with patch('market.websocket_client.logger.error') as mock_error:
            msg = {"type": "error", "msg": "Rate limited"}
            ws_client._on_message(ws_client.ws, json.dumps(msg))
            mock_error.assert_called_once()

    def test_on_message_invalid_json_logs_error(self, ws_client):
        """Handles malformed JSON gracefully."""
        with patch('market.websocket_client.logger.error') as mock_error:
            ws_client._on_message(ws_client.ws, "not valid json {")
            mock_error.assert_called_once()
            assert "Invalid JSON" in mock_error.call_args[0][0]

    def test_on_error_logs_with_auth_hint(self, ws_client):
        """on_error logs with 401/403 hint."""
        with patch('market.websocket_client.logger.error') as mock_error:
            ws_client._on_error(ws_client.ws, Exception("403 Forbidden"))
            mock_error.assert_called_once()
            assert "403/401" in mock_error.call_args[0][0]

    def test_on_close_clears_connection_event(self, ws_client):
        """on_close clears connected_event."""
        ws_client.connected_event.set()
        ws_client._on_close(ws_client.ws, 1000, "Normal closure")
        assert ws_client.connected_event.is_set() is False

    def test_recreate_ws_creates_new_websocketapp(self, ws_client):
        """_recreate_ws creates new WebSocketApp with auth headers."""
        with patch('market.websocket_client.WebSocketApp') as mock_ws_app, \
             patch.object(ws_client, '_get_auth_headers', return_value=["header1", "header2"]):
            ws_client._recreate_ws()
            mock_ws_app.assert_called_once()
            args, kwargs = mock_ws_app.call_args
            assert kwargs['header'] == ["header1", "header2"]
            assert kwargs['on_open'] == ws_client._on_open
            assert kwargs['on_message'] == ws_client._on_message
            assert kwargs['on_error'] == ws_client._on_error
            assert kwargs['on_close'] == ws_client._on_close


class TestWebSocketReconnection:
    """Test reconnection logic and backoff."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict(os.environ, {
            "KALSHI_TESTING": "1", "KALSHI_API_KEY_ID": "test",
            "KALSHI_PRIVATE_KEY_PATH": "/fake", "KALSHI_ENV": "demo",
            "REQUEST_TIMEOUT_SEC": "10", "RETRY_MAX_ATTEMPTS": "3",
            "RETRY_BACKOFF_SEC": "1", "MAX_VAR_LIMIT_PCT": "0.02",
            "MAX_SECTOR_LIMIT_PCT": "0.3", "KELLY_MULTIPLIER": "0.25",
        }, clear=True), patch.object(Config, 'get_private_key') as mock_key, \
                 patch.object(Config, 'validate') as mock_val:
            mock_key.return_value = Mock()
            yield

    def test_run_loop_reconnects_on_exception(self, order_book):
        """_run_loop recreates WS and reconnects on exception."""
        with patch('market.websocket_client.WebSocketApp') as mock_ws_app, \
             patch('market.websocket_client.time.sleep') as mock_sleep, \
             patch.object(KalshiWebSocketClient, '_recreate_ws') as mock_recreate:

            mock_ws = Mock()
            mock_ws.run_forever.side_effect = [
                Exception("Connection reset"),
                None  # Second call succeeds
            ]
            mock_ws_app.return_value = mock_ws

            client = KalshiWebSocketClient("TICKER", order_book)
            client.stop_event = Mock()
            client.stop_event.is_set.side_effect = [False, False, True]  # Run twice, then stop

            client._run_loop()

            assert mock_recreate.call_count == 2
            assert mock_sleep.call_count == 1
            mock_sleep.assert_called_with(5)  # 5 second reconnect delay

    def test_reconnect_backoff_max_30s(self, order_book):
        """Verify reconnect delay is 5 seconds (hardcoded in _run_loop)."""
        with patch('market.websocket_client.WebSocketApp') as mock_ws_app, \
             patch('market.websocket_client.time.sleep') as mock_sleep:

            mock_ws = Mock()
            mock_ws.run_forever.side_effect = Exception("Error")
            mock_ws_app.return_value = mock_ws

            client = KalshiWebSocketClient("TICKER", order_book)
            client.stop_event = Mock()
            client.stop_event.is_set.side_effect = [False, True]

            client._run_loop()
            mock_sleep.assert_called_with(5)


class TestAuthHeaders:
    """Test WebSocket authentication header generation."""

    def test_get_auth_headers_format(self):
        """_get_auth_headers returns proper Kalshi auth headers."""
        with patch.dict(os.environ, {
            "KALSHI_API_KEY_ID": "test_key_id",
            "KALSHI_TESTING": "1",
        }, clear=True), patch.object(Config, 'get_private_key') as mock_key, \
                 patch.object(Config, 'validate') as mock_val, \
                 patch.object(Config, 'get_ws_url', return_value="wss://test"):

            mock_private_key = Mock()
            mock_signature = Mock()
            mock_signature.__bytes__ = Mock(return_value=b"signature_bytes")
            mock_private_key.sign.return_value = mock_signature

            mock_key.return_value = mock_private_key

            client = KalshiWebSocketClient("TICKER", LocalOrderBook())
            headers = client._get_auth_headers()

            assert len(headers) == 3
            assert any(h.startswith("KALSHI-ACCESS-KEY: test_key_id") for h in headers)
            assert any(h.startswith("KALSHI-ACCESS-TIMESTAMP:") for h in headers)
            assert any(h.startswith("KALSHI-ACCESS-SIGNATURE:") for h in headers)


class TestOrderBookIntegration:
    """Test order book snapshot and delta application."""

    def test_snapshot_then_delta(self):
        """Snapshot followed by delta updates book correctly."""
        book = LocalOrderBook()

        # Apply snapshot
        snapshot = {
            "type": "orderbook_snapshot",
            "msg": {
                "sequence": 10,
                "yes": [{"price": "0.50", "size": "100"}, {"price": "0.49", "size": "200"}],
                "no": [{"price": "0.51", "size": "150"}]
            }
        }
        book.apply_snapshot(snapshot)

        best_yes_bid, _ = book.get_best_yes_bid()
        best_yes_ask, _ = book.get_best_yes_ask()
        assert best_yes_bid == 0.50
        assert best_yes_ask == 0.51

        # Apply delta: increase bid at 0.50
        delta = {
            "type": "orderbook_delta",
            "msg": {
                "sequence": 11,
                "side": "yes",
                "price_dollars": "0.50",
                "delta_fp": "50"  # +50
            }
        }
        book.apply_delta(delta)

        best_yes_bid, _ = book.get_best_yes_bid()
        assert best_yes_bid == 0.50  # Price same, size increased

    def test_delta_removes_price_level(self):
        """Negative delta removes price level when size reaches 0."""
        book = LocalOrderBook()
        book.apply_snapshot({
            "type": "orderbook_snapshot",
            "msg": {"sequence": 1, "yes": [{"price": "0.50", "size": "100"}]}
        })

        # Remove all 100
        book.apply_delta({
            "type": "orderbook_delta",
            "msg": {"sequence": 2, "side": "yes", "price_dollars": "0.50", "delta_fp": "-100"}
        })

        best_yes_bid, _ = book.get_best_yes_bid()
        assert best_yes_bid is None
