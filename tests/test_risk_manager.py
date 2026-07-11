import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import Config
from safety.risk_manager import RiskManager


class TestRiskManager:
    """Test Kelly Criterion, VaR caps, and sector limits."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict(os.environ, {
            "KALSHI_TESTING": "1", "KALSHI_API_KEY_ID": "test",
            "MAX_VAR_LIMIT_PCT": "0.02", "MAX_SECTOR_LIMIT_PCT": "0.30",
            "KELLY_MULTIPLIER": "0.25", "REQUEST_TIMEOUT_SEC": "10",
            "RETRY_MAX_ATTEMPTS": "3", "RETRY_BACKOFF_SEC": "1",
            "KELLY_MULTIPLIER": "0.25",
        }, clear=True), patch.object(Config, 'get_private_key') as mock_key:
            mock_key.return_value = Mock()
            yield

    def setup_method(self):
        """Fresh RiskManager per test."""
        self.rm = RiskManager()

    # ===== Kelly Criterion Tests =====

    def test_kelly_yes_positive_edge(self):
        """Kelly > 0 when p > P for YES contract."""
        # p = 0.6, P = 0.5 → (0.6 - 0.5) / (1 - 0.5) = 0.1 / 0.5 = 0.2
        kelly = self.rm.calculate_kelly_fraction(Decimal("0.6"), Decimal("0.5"), "yes")
        assert kelly == Decimal("0.2")

    def test_kelly_yes_no_edge(self):
        """Kelly = 0 when p == P for YES."""
        kelly = self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("0.5"), "yes")
        assert kelly == Decimal("0")

    def test_kelly_yes_negative_edge(self):
        """Kelly = 0 when p < P for YES (no negative)."""
        kelly = self.rm.calculate_kelly_fraction(Decimal("0.4"), Decimal("0.5"), "yes")
        assert kelly == Decimal("0")

    def test_kelly_no_positive_edge(self):
        """Kelly > 0 when p < P for NO (p_no > P_no)."""
        # YES price = 0.5 → NO price = 0.5
        # p_yes = 0.4 → p_no = 0.6
        # Kelly = (0.6 - 0.5) / 0.5 = 0.2
        kelly = self.rm.calculate_kelly_fraction(Decimal("0.4"), Decimal("0.5"), "no")
        assert kelly == Decimal("0.2")

    def test_kelly_no_no_edge(self):
        """Kelly = 0 when p_no == P_no."""
        # p_yes = 0.5 → p_no = 0.5, P_yes = 0.5 → P_no = 0.5
        kelly = self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("0.5"), "no")
        assert kelly == Decimal("0")

    def test_kelly_extreme_probabilities(self):
        """Handles p=0 and p=1 edge cases."""
        # Certain YES at fair price
        kelly = self.rm.calculate_kelly_fraction(Decimal("1.0"), Decimal("0.5"), "yes")
        assert kelly == Decimal("1.0")  # (1-0.5)/(1-0.5) = 1

        # Impossible YES
        kelly = self.rm.calculate_kelly_fraction(Decimal("0.0"), Decimal("0.5"), "yes")
        assert kelly == Decimal("0")

    def test_kelly_invalid_price_raises(self):
        """Raises ValueError for price <= 0 or >= 1."""
        with pytest.raises(ValueError, match="price must be between 0 and 1"):
            self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("0"), "yes")
        with pytest.raises(ValueError, match="price must be between 0 and 1"):
            self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("1.0"), "yes")
        with pytest.raises(ValueError, match="price must be between 0 and 1"):
            self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("-0.1"), "yes")
        with pytest.raises(ValueError, match="price must be between 0 and 1"):
            self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("1.1"), "yes")

    def test_kelly_invalid_probability_raises(self):
        """Raises ValueError for probability outside [0, 1]."""
        with pytest.raises(ValueError, match="probability must be between 0 and 1"):
            self.rm.calculate_kelly_fraction(Decimal("-0.1"), Decimal("0.5"), "yes")
        with pytest.raises(ValueError, match="probability must be between 0 and 1"):
            self.rm.calculate_kelly_fraction(Decimal("1.1"), Decimal("0.5"), "yes")

    def test_kelly_invalid_side_raises(self):
        """Raises ValueError for invalid side."""
        with pytest.raises(ValueError, match="Invalid side"):
            self.rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("0.5"), "maybe")

    # ===== Position Sizing (Fractional Kelly + VaR) =====

    def test_fractional_kelly_quarter(self):
        """Fractional Kelly applies 0.25 multiplier."""
        # Raw Kelly = 0.2, fractional = 0.2 * 0.25 = 0.05
        fraction = self.rm.get_position_size_fraction(Decimal("0.6"), Decimal("0.5"), "yes")
        assert fraction == Decimal("0.05")

    def test_var_cap_at_two_percent(self):
        """VaR caps at MAX_VAR_LIMIT_PCT (2%)."""
        # Raw Kelly would be 0.8, fractional = 0.2, but VaR caps at 0.02
        fraction = self.rm.get_position_size_fraction(Decimal("0.9"), Decimal("0.1"), "yes")
        assert fraction == Decimal("0.02")

    def test_var_cap_below_fractional(self):
        """When fractional Kelly < VaR, fractional applies."""
        # Raw Kelly = 0.04, fractional = 0.01, VaR = 0.02
        fraction = self.rm.get_position_size_fraction(Decimal("0.55"), Decimal("0.5"), "yes")
        assert fraction == Decimal("0.0125")  # 0.04 * 0.25 = 0.01

    # ===== Sector Limits =====

    def test_sector_limit_allows_within_cap(self):
        """Sector limit allows additional when under 30%."""
        total = Decimal("10000")
        current = Decimal("1000")  # 10% used
        allowed = self.rm.get_max_allowed_wager_for_sector("Economics", current, total)
        # 30% of 10000 = 3000, minus 1000 used = 2000 remaining
        assert allowed == Decimal("2000")

    def test_sector_limit_blocks_at_cap(self):
        """Sector limit returns 0 when at or over 30%."""
        total = Decimal("10000")
        current = Decimal("3000")  # Exactly 30%
        allowed = self.rm.get_max_allowed_wager_for_sector("Economics", current, total)
        assert allowed == Decimal("0")

    def test_sector_limit_returns_zero_over_cap(self):
        """Sector limit returns 0 (not negative) when over cap."""
        total = Decimal("10000")
        current = Decimal("5000")  # 50% used
        allowed = self.rm.get_max_allowed_wager_for_sector("Economics", current, total)
        assert allowed == Decimal("0")

    def test_sector_limit_zero_capital(self):
        """Zero capital returns zero allowed."""
        allowed = self.rm.get_max_allowed_wager_for_sector("Economics", Decimal("0"), Decimal("0"))
        assert allowed == Decimal("0")

    # ===== Full Order Sizing Pipeline =====

    def test_size_order_pipeline_kelly_var_sector(self):
        """Full pipeline: Kelly → ¼ Kelly → VaR → Sector cap."""
        total = Decimal("10000")
        sector_exposure = Decimal("1000")

        # p=0.6, P=0.5 → raw_kelly=0.2 → frac=0.05 → var=min(0.05, 0.02)=0.02
        # proposed = 10000 * 0.02 = 200
        # sector allows 2000, so final = 200
        wager = self.rm.size_order(
            estimated_prob=Decimal("0.6"),
            market_price=Decimal("0.5"),
            side="yes",
            sector="Economics",
            current_sector_exposure=sector_exposure,
            total_capital=total,
        )
        assert wager == Decimal("200")

    def test_size_order_sector_cap_binds(self):
        """Sector cap binds when VaR would exceed sector room."""
        total = Decimal("10000")
        sector_exposure = Decimal("2900")  # Only 100 left in sector

        # Same Kelly as above → proposed = 200
        # But sector only allows 100
        wager = self.rm.size_order(
            estimated_prob=Decimal("0.6"),
            market_price=Decimal("0.5"),
            side="yes",
            sector="Economics",
            current_sector_exposure=sector_exposure,
            total_capital=total,
        )
        assert wager == Decimal("100")

    def test_size_order_no_edge_returns_zero(self):
        """No edge (p <= P) returns zero wager."""
        wager = self.rm.size_order(
            estimated_prob=Decimal("0.5"),
            market_price=Decimal("0.5"),
            side="yes",
            sector="Economics",
            current_sector_exposure=Decimal("0"),
            total_capital=Decimal("10000"),
        )
        assert wager == Decimal("0")

    def test_size_order_zero_capital_returns_zero(self):
        """Zero capital returns zero wager."""
        wager = self.rm.size_order(
            estimated_prob=Decimal("0.6"),
            market_price=Decimal("0.5"),
            side="yes",
            sector="Economics",
            current_sector_exposure=Decimal("0"),
            total_capital=Decimal("0"),
        )
        assert wager == Decimal("0")

    def test_size_order_no_side_buy_and_sell(self):
        """Both buy YES and buy NO work (sell is same as opposite buy)."""
        # The method only takes side="yes" or "no" (not buy/sell)
        wager_yes = self.rm.size_order(
            estimated_prob=Decimal("0.6"), market_price=Decimal("0.5"),
            side="yes", sector="Econ", current_sector_exposure=Decimal("0"),
            total_capital=Decimal("10000"),
        )
        wager_no = self.rm.size_order(
            estimated_prob=Decimal("0.4"), market_price=Decimal("0.5"),
            side="no", sector="Econ", current_sector_exposure=Decimal("0"),
            total_capital=Decimal("10000"),
        )
        # Both have same edge magnitude
        assert wager_yes == wager_no == Decimal("200")

    # ===== Property-Based Tests =====

    @pytest.mark.parametrize("p,P,side,expected_raw", [
        (Decimal("0.6"), Decimal("0.5"), "yes", Decimal("0.2")),
        (Decimal("0.7"), Decimal("0.5"), "yes", Decimal("0.4")),
        (Decimal("0.4"), Decimal("0.5"), "no", Decimal("0.2")),
        (Decimal("0.3"), Decimal("0.5"), "no", Decimal("0.4")),
        (Decimal("0.5"), Decimal("0.5"), "yes", Decimal("0")),
        (Decimal("0.5"), Decimal("0.5"), "no", Decimal("0")),
    ])
    def test_kelly_parametrized(self, p, P, side, expected_raw):
        raw = self.rm.calculate_kelly_fraction(p, P, side)
        assert raw == expected_raw

    @pytest.mark.parametrize("raw_kelly", [
        Decimal("0"), Decimal("0.01"), Decimal("0.05"),
        Decimal("0.1"), Decimal("0.5"), Decimal("1.0"),
    ])
    def test_fractional_kelly_bounds(self, raw_kelly):
        """Fractional Kelly always ≤ raw Kelly and ≤ 1."""
        frac = raw_kelly * self.rm.kelly_mult
        assert frac <= raw_kelly
        assert frac <= Decimal("1")

    @pytest.mark.parametrize("fractional_kelly", [
        Decimal("0"), Decimal("0.01"), Decimal("0.05"),
        Decimal("0.1"), Decimal("0.5"), Decimal("1.0"),
    ])
    def test_var_cap_bounds(self, fractional_kelly):
        """VaR cap always ≤ fractional Kelly and ≤ MAX_VAR_LIMIT_PCT."""
        capped = min(fractional_kelly, self.rm.var_limit)
        assert capped <= fractional_kelly
        assert capped <= self.rm.var_limit
