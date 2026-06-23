import os

os.environ["KALSHI_TESTING"] = "1"

import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import Config
from data.database import (
    get_connection,
    get_strategy_performance_summary,
    has_active_order,
    initialize_db,
    log_strategy_signal,
    store_order,
)
from execution.engine import ExecutionEngine, _close_shadow_logger
from execution.order_state import Order, OrderAction, OrderSide, OrderState, get_order_state_machine
from execution.position_manager import get_position_manager
from market.order_book import LocalOrderBook
from safety.risk_manager import RiskManager
from strategy.macro_tracker import MockCalendarProvider


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    saved_path = Config.DATABASE_PATH
    Config.DATABASE_PATH = str(tmp_path / "test.db")
    initialize_db()
    yield
    Config.DATABASE_PATH = saved_path


@pytest.fixture(autouse=True)
def reset_singletons(fresh_db):
    get_order_state_machine()._orders.clear()
    get_position_manager()._positions.clear()
    _close_shadow_logger()
    yield
    _close_shadow_logger()


class TestFullShadowTradeFlow:
    def test_full_trade_flow_yes_side(self) -> None:
        orig = Config.SHADOW_MODE
        Config.SHADOW_MODE = True

        rm = RiskManager()
        engine = ExecutionEngine()
        book = LocalOrderBook()

        book.apply_snapshot({
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "CPI-25JAN",
                "market_id": "test-id",
                "yes_dollars_fp": [["0.5800", "500.00"]],
                "no_dollars_fp": [["0.4100", "300.00"]],
            },
        })

        result = MockCalendarProvider.trigger_mock_release(
            indicator="CPI",
            actual=3.5,
            forecast=3.0,
            previous=3.1,
            ticker="CPI-25JAN",
            sector="Economics",
            risk_manager=rm,
            execution_engine=engine,
            order_book=book,
            total_capital=Decimal("10000.00"),
            surprise_std=0.10,
        )

        Config.SHADOW_MODE = orig

        assert result["status"] == "executed"
        assert result["side"] == "yes"
        assert result["wager"] > 0
        assert result["quantity"] > 0

        summary = get_strategy_performance_summary("CPI")
        assert len(summary) >= 1

    def test_full_trade_flow_no_side(self) -> None:
        orig = Config.SHADOW_MODE
        Config.SHADOW_MODE = True

        rm = RiskManager()
        engine = ExecutionEngine()
        book = LocalOrderBook()

        book.apply_snapshot({
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "CPI-25JAN",
                "market_id": "test-id",
                "yes_dollars_fp": [["0.5800", "500.00"]],
                "no_dollars_fp": [["0.4100", "300.00"]],
            },
        })

        result = MockCalendarProvider.trigger_mock_release(
            indicator="PCE",
            actual=2.0,
            forecast=2.5,
            previous=2.4,
            ticker="CPI-25JAN",
            sector="Economics",
            risk_manager=rm,
            execution_engine=engine,
            order_book=book,
            total_capital=Decimal("10000.00"),
            surprise_std=0.08,
        )

        Config.SHADOW_MODE = orig

        assert result["status"] == "executed"
        assert result["side"] == "no"
        assert result["wager"] > 0


class TestPriceRoundingConsistency:
    def test_price_rounded_before_use(self) -> None:
        engine = ExecutionEngine()
        assert engine.format_price(Decimal("0.12345")) == "0.1234"
        assert engine.format_price(Decimal("0.12346")) == "0.1235"

    def test_price_rounded_in_place_order(self) -> None:
        orig = Config.SHADOW_MODE
        Config.SHADOW_MODE = True

        engine = ExecutionEngine()
        engine.fee_tracker.reset()
        resp = engine.place_order(
            ticker="ROUND-TEST",
            outcome_side="yes",
            price=Decimal("0.12346"),
            quantity=Decimal("10"),
            action="buy",
        )

        Config.SHADOW_MODE = orig

        assert resp["status"] == "filled"
        assert resp["price"] == "0.1235"


class TestDuplicateDetection:
    def test_has_active_order_on_non_terminal(self) -> None:
        store_order(
            client_order_id="dup-test-1",
            ticker="DUP-TEST",
            status="submitted",
            action="buy",
            outcome_side="yes",
            price=0.60,
            quantity=10.0,
        )
        assert has_active_order("DUP-TEST", "yes", 0.60, "buy")
        assert not has_active_order("DUP-TEST", "no", 0.60, "buy")
        assert not has_active_order("OTHER", "yes", 0.60, "buy")

    def test_has_active_order_on_terminal(self) -> None:
        store_order(
            client_order_id="dup-test-2",
            ticker="DUP-TEST",
            status="filled",
            action="buy",
            outcome_side="yes",
            price=0.60,
            quantity=10.0,
        )
        assert has_active_order("DUP-TEST", "yes", 0.60, "buy")

    def test_engine_skips_duplicate_by_content(self) -> None:
        orig = Config.SHADOW_MODE
        Config.SHADOW_MODE = True

        engine = ExecutionEngine()
        engine.fee_tracker.reset()

        resp1 = engine.place_order(
            ticker="DUP-CONTENT",
            outcome_side="yes",
            price=Decimal("0.60"),
            quantity=Decimal("10"),
            action="buy",
        )
        assert resp1["status"] == "filled"

        resp2 = engine.place_order(
            ticker="DUP-CONTENT",
            outcome_side="yes",
            price=Decimal("0.60"),
            quantity=Decimal("10"),
            action="buy",
        )

        Config.SHADOW_MODE = orig

        assert resp2["status"] == "skipped_duplicate"

    def test_engine_skips_duplicate_by_client_id(self) -> None:
        orig = Config.SHADOW_MODE
        Config.SHADOW_MODE = True

        engine = ExecutionEngine()
        engine.fee_tracker.reset()

        resp1 = engine.place_order(
            ticker="DUP-CID",
            outcome_side="no",
            price=Decimal("0.40"),
            quantity=Decimal("5"),
            action="buy",
        )
        assert resp1["status"] == "filled"

        resp2 = engine.place_order(
            ticker="DUP-CID",
            outcome_side="no",
            price=Decimal("0.40"),
            quantity=Decimal("5"),
            action="buy",
        )

        Config.SHADOW_MODE = orig

        assert resp2["status"] == "skipped_duplicate"


class TestStrategyPerformanceSummary:
    def test_empty_when_no_signals(self) -> None:
        result = get_strategy_performance_summary()
        assert result == []

    def test_aggregates_correctly(self) -> None:
        log_strategy_signal("CPI", 3.0, 3.2, 0.2, 1.5, "good", 0.65, "yes", 200.0, profitable=True)
        log_strategy_signal("CPI", 3.0, 3.1, 0.1, 1.0, "fair", 0.60, "yes", 150.0, profitable=True)
        log_strategy_signal("CPI", 3.0, 2.8, -0.2, 1.5, "good", 0.65, "no", 100.0, profitable=False)
        log_strategy_signal(
            "FOMC", 5.0, 5.5, 0.5, 2.0, "strong", 0.75, "yes", 300.0, profitable=True
        )

        result = get_strategy_performance_summary()
        cpi = [r for r in result if r["indicator"] == "CPI"]
        fomc = [r for r in result if r["indicator"] == "FOMC"]
        assert len(cpi) == 1
        assert len(fomc) == 1

        assert cpi[0]["total_signals"] == 3
        assert cpi[0]["wins"] == 2
        assert cpi[0]["losses"] == 1
        assert cpi[0]["total_wager"] == 450.0

        assert fomc[0]["total_signals"] == 1
        assert fomc[0]["wins"] == 1
        assert fomc[0]["losses"] == 0
        assert fomc[0]["total_wager"] == 300.0

    def test_filter_by_indicator(self) -> None:
        log_strategy_signal("CPI", 3.0, 3.2, 0.2, 1.5, "good", 0.65, "yes", 200.0, profitable=True)
        log_strategy_signal(
            "FOMC", 5.0, 5.5, 0.5, 2.0, "strong", 0.75, "yes", 300.0, profitable=True
        )

        filtered = get_strategy_performance_summary("CPI")
        assert len(filtered) == 1
        assert filtered[0]["indicator"] == "CPI"
        assert filtered[0]["total_signals"] == 1

    def test_unresolved_signals_included_in_total(self) -> None:
        log_strategy_signal("CPI", 3.0, 3.2, 0.2, 1.5, "good", 0.65, "yes", 200.0, profitable=True)
        log_strategy_signal("CPI", 3.0, 3.1, 0.1, 1.0, "fair", 0.60, "yes", 150.0)
        log_strategy_signal("CPI", 3.0, 2.8, -0.2, 1.5, "good", 0.65, "no", 100.0, profitable=False)

        result = get_strategy_performance_summary("CPI")
        assert len(result) == 1
        assert result[0]["total_signals"] == 3
        assert result[0]["wins"] == 1
        assert result[0]["losses"] == 1


class TestOrderRecovery:
    def test_restore_orders_from_db(self) -> None:
        store_order(
            client_order_id="recover-1",
            ticker="REC-TEST",
            status="submitted",
            action="buy",
            outcome_side="yes",
            price=0.60,
            quantity=10.0,
        )
        store_order(
            client_order_id="recover-2",
            ticker="REC-TEST",
            status="partial",
            action="buy",
            outcome_side="no",
            price=0.40,
            quantity=5.0,
        )
        store_order(
            client_order_id="recover-3",
            ticker="REC-TEST",
            status="filled",
            action="sell",
            outcome_side="yes",
            price=0.70,
            quantity=10.0,
        )

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT client_order_id, ticker, status, action, outcome_side,
                   price, quantity, kalshi_order_id
            FROM orders
            WHERE status IN ('pending', 'submitted', 'partial')
        """
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) == 2

        sm = get_order_state_machine()
        sm._orders.clear()
        for row in rows:
            sm._orders[f"restored-{row[0]}"] = Order(
                id=f"restored-{row[0]}",
                client_order_id=row[0],
                ticker=row[1],
                side=OrderSide.YES if row[4] == "yes" else OrderSide.NO,
                action=OrderAction(row[3]),
                price=Decimal(str(row[5])),
                quantity=Decimal(str(row[6])),
                kalshi_order_id=row[7],
                state=OrderState(row[2]),
            )

        open_orders = sm.get_open_orders()
        assert len(open_orders) == 2
        cids = {o.client_order_id for o in open_orders}
        assert "recover-1" in cids
        assert "recover-2" in cids
