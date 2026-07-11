import os
import sys
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import Config


def pytest_configure(config):
    """Set up default Config patches for all tests."""
    Config.API_KEY_ID = "test_key"
    Config.PRIVATE_KEY_PATH = "/fake/path"
    Config.ENV = "demo"
    Config.SHADOW_MODE = True
    Config.REQUEST_TIMEOUT_SEC = 10.0
    Config.RETRY_MAX_ATTEMPTS = 3
    Config.RETRY_BACKOFF_SEC = 1.0
    Config.MAX_VAR_LIMIT_PCT = Decimal("0.02")
    Config.MAX_SECTOR_LIMIT_PCT = Decimal("0.30")
    Config.KELLY_MULTIPLIER = Decimal("0.25")
    Config.API_VERSION = "v2"
    Config.API_BASE_PATH = "/trade-api/v2"
    Config.FRED_API_KEY = ""
    Config.ALPHA_VANTAGE_API_KEY = ""
    Config.CONVICTION_SLOPE = 0.12
    Config.CONVICTION_MAX_DELTA = 0.35
    Config.MIN_CONVICTION_SIGMA = 1.0
    Config.MAX_SPREAD_PCT = Decimal("0.05")
    Config.TRADE_COOLDOWN_SEC = 3600.0
    Config.KILL_SWITCH_MIN_BALANCE = Decimal("100.00")
    Config.DATABASE_PATH = "/fake/path/kalshi_shadow.db"
    Config._private_key = Mock()
    Config._session = Mock()


@pytest.fixture(autouse=True)
def patch_config():
    """Auto-patch Config for all tests, yielding mock for request_with_retry."""
    with patch.object(Config, 'get_private_key') as mock_key, \
         patch.object(Config, 'validate', return_value=None), \
         patch.object(Config, 'get_verified_session', return_value=Mock()), \
         patch.object(Config, 'get_rest_url', return_value="https://demo.api"), \
         patch.object(Config, 'request_with_retry') as mock_req:
        mock_key.return_value = Mock()
        yield mock_req


@pytest.fixture
def mock_session():
    return Mock()
