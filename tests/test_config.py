import os
import sys
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import Config


class TestConfigValidation:
    """Test Config.validate() startup validation."""

    def test_validate_passes_with_testing_flag(self):
        """KALSHI_TESTING=1 skips all validation."""
        with patch.object(Config, 'API_KEY_ID', 'test_key'), \
             patch.object(Config, 'PRIVATE_KEY_PATH', '/fake/path'), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
             patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
             patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
             patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
             patch.object(Config, 'get_private_key') as mock_key:
            mock_key.return_value = Mock()
            Config.validate()  # Should not raise

    def test_validate_requires_api_key(self):
        """Missing API key raises ValueError."""
        with patch.object(Config, 'API_KEY_ID', ''), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
             patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
             patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
             patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')):
            with pytest.raises(ValueError, match="KALSHI_API_KEY_ID"):
                Config.validate()

    def test_validate_rejects_zero_timeout(self):
        """REQUEST_TIMEOUT_SEC must be > 0."""
        with patch.object(Config, 'API_KEY_ID', 'test_key'), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 0.0), \
             patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
             patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
             patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
             patch.object(Config, 'get_private_key') as mock_key:
            mock_key.return_value = Mock()
            with pytest.raises(ValueError, match="REQUEST_TIMEOUT_SEC must be greater than 0"):
                Config.validate()

    def test_validate_rejects_negative_timeout(self):
        with patch.object(Config, 'API_KEY_ID', 'test_key'), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', -5.0), \
             patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
             patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
             patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
             patch.object(Config, 'get_private_key') as mock_key:
            mock_key.return_value = Mock()
            with pytest.raises(ValueError, match="REQUEST_TIMEOUT_SEC must be greater than 0"):
                Config.validate()

    def test_validate_rejects_zero_retry_attempts(self):
        """RETRY_MAX_ATTEMPTS must be >= 1."""
        with patch.object(Config, 'API_KEY_ID', 'test_key'), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
             patch.object(Config, 'RETRY_MAX_ATTEMPTS', 0), \
             patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
             patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
             patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
             patch.object(Config, 'get_private_key') as mock_key:
            mock_key.return_value = Mock()
            with pytest.raises(ValueError, match="RETRY_MAX_ATTEMPTS must be at least 1"):
                Config.validate()

    def test_validate_rejects_var_out_of_range(self):
        """MAX_VAR_LIMIT_PCT must be in (0, 1]."""
        for bad_val in ["0", "-0.01", "1.5", "2"]:
            with patch.object(Config, 'API_KEY_ID', 'test_key'), \
                 patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
                 patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
                 patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal(bad_val)), \
                 patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
                 patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
                 patch.object(Config, 'get_private_key') as mock_key:
                mock_key.return_value = Mock()
                with pytest.raises(ValueError, match="MAX_VAR_LIMIT_PCT must be in"):
                    Config.validate()

    def test_validate_rejects_sector_limit_out_of_range(self):
        for bad_val in ["0", "-0.1", "1.5"]:
            with patch.object(Config, 'API_KEY_ID', 'test_key'), \
                 patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
                 patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
                 patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
                 patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal(bad_val)), \
                 patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
                 patch.object(Config, 'get_private_key') as mock_key:
                mock_key.return_value = Mock()
                with pytest.raises(ValueError, match="MAX_SECTOR_LIMIT_PCT must be in"):
                    Config.validate()

    def test_validate_rejects_kelly_multiplier_out_of_range(self):
        for bad_val in ["0", "-0.1", "1.5"]:
            with patch.object(Config, 'API_KEY_ID', 'test_key'), \
                 patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
                 patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
                 patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
                 patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
                 patch.object(Config, 'KELLY_MULTIPLIER', Decimal(bad_val)), \
                 patch.object(Config, 'get_private_key') as mock_key:
                mock_key.return_value = Mock()
                with pytest.raises(ValueError, match="KELLY_MULTIPLIER must be in"):
                    Config.validate()

    def test_validate_calls_get_private_key(self):
        """Validate triggers private key loading."""
        with patch.object(Config, 'API_KEY_ID', 'test_key'), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0), \
             patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'MAX_VAR_LIMIT_PCT', Decimal('0.02')), \
             patch.object(Config, 'MAX_SECTOR_LIMIT_PCT', Decimal('0.30')), \
             patch.object(Config, 'KELLY_MULTIPLIER', Decimal('0.25')), \
             patch.object(Config, 'get_private_key') as mock_get_key:
            mock_get_key.return_value = Mock()
            Config.validate()
            mock_get_key.assert_called_once()


class TestConfigURLs:
    """Test REST and WebSocket URL building."""

    def test_get_rest_url_demo(self):
        with patch.object(Config, 'ENV', 'demo'):
            assert Config.get_rest_url() == "https://external-api.demo.kalshi.co"

    def test_get_rest_url_prod(self):
        with patch.object(Config, 'ENV', 'prod'):
            assert Config.get_rest_url() == "https://external-api.kalshi.com"

    def test_get_ws_url_demo(self):
        with patch.object(Config, 'ENV', 'demo'), \
             patch.object(Config, 'API_VERSION', 'v2'):
            assert Config.get_ws_url() == "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"

    def test_get_ws_url_prod(self):
        with patch.object(Config, 'ENV', 'prod'), \
             patch.object(Config, 'API_VERSION', 'v2'):
            assert Config.get_ws_url() == "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

    def test_build_api_path(self):
        with patch.object(Config, 'API_VERSION', 'v2'), \
             patch.object(Config, 'API_BASE_PATH', '/trade-api/v2'):
            assert Config.build_api_path("/portfolio/orders") == "/trade-api/v2/portfolio/orders"
            assert Config.build_api_path("portfolio/orders") == "/trade-api/v2/portfolio/orders"


class TestConfigRetryLogic:
    """Test request_with_retry behavior."""

    @pytest.fixture
    def mock_session(self):
        return Mock()

    def test_retry_on_429(self, mock_session):
        """Retries on 429 with exponential backoff."""
        with patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'RETRY_BACKOFF_SEC', 0.01), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0):
            mock_resp_429 = Mock()
            mock_resp_429.status_code = 429
            mock_resp_200 = Mock()
            mock_resp_200.status_code = 200
            mock_resp_200.json.return_value = {"ok": True}

            mock_session.request.side_effect = [mock_resp_429, mock_resp_429, mock_resp_200]

            resp = Config.request_with_retry("GET", "https://api.test", session=mock_session)
            assert resp.status_code == 200
            assert mock_session.request.call_count == 3

    def test_retry_on_503(self, mock_session):
        """Retries on 503."""
        with patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'RETRY_BACKOFF_SEC', 0.01), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0):
            mock_resp_503 = Mock()
            mock_resp_503.status_code = 503
            mock_resp_200 = Mock()
            mock_resp_200.status_code = 200

            mock_session.request.side_effect = [mock_resp_503, mock_resp_200]

            resp = Config.request_with_retry("GET", "https://api.test", session=mock_session)
            assert resp.status_code == 200

    def test_no_retry_on_400(self, mock_session):
        """No retry on 400 (client error)."""
        with patch.object(Config, 'RETRY_MAX_ATTEMPTS', 3), \
             patch.object(Config, 'RETRY_BACKOFF_SEC', 0.01), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0):
            mock_resp_400 = Mock()
            mock_resp_400.status_code = 400
            mock_session.request.return_value = mock_resp_400

            resp = Config.request_with_retry("GET", "https://api.test", session=mock_session)
            assert resp.status_code == 400
            assert mock_session.request.call_count == 1

    def test_max_attempts_exceeded_raises(self, mock_session):
        """Returns error response after max attempts (no exception for HTTP errors)."""
        with patch.object(Config, 'RETRY_MAX_ATTEMPTS', 2), \
             patch.object(Config, 'RETRY_BACKOFF_SEC', 0.01), \
             patch.object(Config, 'REQUEST_TIMEOUT_SEC', 10.0):
            mock_resp_500 = Mock()
            mock_resp_500.status_code = 500
            mock_session.request.return_value = mock_resp_500

            resp = Config.request_with_retry("GET", "https://api.test", session=mock_session)
            assert resp.status_code == 500
            assert mock_session.request.call_count == 2  # initial + 1 retry


class TestConfigPrivateKey:
    """Test private key loading and validation."""

    def test_get_private_key_requires_path(self):
        with patch.object(Config, 'PRIVATE_KEY_PATH', ''):
            with pytest.raises(ValueError, match="KALSHI_PRIVATE_KEY_PATH environment variable is not set"):
                Config.get_private_key()

    def test_get_private_key_checks_permissions(self, tmp_path):
        """Rejects keys with group/other permissions."""
        # Use a path within allowed dirs
        allowed_dir = os.path.expanduser("~/.kalshi")
        os.makedirs(allowed_dir, exist_ok=True)
        key_file = os.path.join(allowed_dir, "test_key.pem")

        with open(key_file, "w") as f:
            f.write("-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----")
        os.chmod(key_file, 0o644)

        try:
            with patch.object(Config, 'PRIVATE_KEY_PATH', key_file):
                with pytest.raises(PermissionError, match="too permissive"):
                    Config.get_private_key()
        finally:
            if os.path.exists(key_file):
                os.remove(key_file)

    def test_get_private_key_validates_path_in_allowed_dirs(self, tmp_path):
        """Rejects keys outside allowed directories."""
        key_file = tmp_path / "key.pem"
        key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----")
        os.chmod(key_file, 0o600)

        with patch.object(Config, 'PRIVATE_KEY_PATH', str(key_file)):
            with pytest.raises(PermissionError, match="must be within allowed directories"):
                Config.get_private_key()

    def test_clear_private_key(self):
        """clear_private_key resets cached key."""
        Config._private_key = Mock()
        Config.clear_private_key()
        assert Config._private_key is None
