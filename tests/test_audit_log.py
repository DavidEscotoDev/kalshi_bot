import os
import sys
import tempfile
from collections.abc import Iterator

os.environ["KALSHI_TESTING"] = "1"
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from data.audit_log import AuditLogger


class TestAuditLogger:
    @pytest.fixture
    def audit(self) -> Iterator[AuditLogger]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield AuditLogger(base_dir=tmpdir)

    def test_initialization(self, audit: AuditLogger) -> None:
        assert audit is not None

    def test_log_order_placed(self, audit: AuditLogger) -> None:
        audit.log_order_placed(
            ticker="TEST-26",
            action="buy",
            outcome_side="yes",
            price=0.65,
            quantity=10.0,
            order_id="ord-123",
        )
        path = audit._log_path()
        assert os.path.exists(path)

    def test_log_order_filled(self, audit: AuditLogger) -> None:
        audit.log_order_filled(
            ticker="TEST-26",
            action="buy",
            outcome_side="yes",
            price=0.65,
            quantity=10.0,
            order_id="ord-123",
            fee=0.01,
            rebate=0.0,
        )
        assert os.path.exists(audit._log_path())

    def test_log_order_rejected(self, audit: AuditLogger) -> None:
        audit.log_order_rejected(
            ticker="TEST-26",
            action="buy",
            outcome_side="yes",
            reason="insufficient_balance",
            order_id="ord-123",
        )
        assert os.path.exists(audit._log_path())

    def test_log_kill_switch(self, audit: AuditLogger) -> None:
        audit.log_kill_switch(reason="balance_too_low", balance=50.0)
        assert os.path.exists(audit._log_path())

    def test_log_strategy_trigger(self, audit: AuditLogger) -> None:
        audit.log_strategy_trigger(
            ticker="FED-26",
            indicator="CPI",
            actual=3.2,
            forecast=3.0,
            wager=200.0,
            side="yes",
        )
        assert os.path.exists(audit._log_path())

    def test_multiple_entries_append(self, audit: AuditLogger) -> None:
        for i in range(10):
            audit.log("test", "TICKER", "buy", iteration=i)
        path = audit._log_path()
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 10

    def test_log_with_extra_fields(self, audit: AuditLogger) -> None:
        audit.log(
            event_type="custom_event",
            ticker="TEST",
            action="custom",
            extra_field_1="value1",
            extra_field_2=42,
        )
        path = audit._log_path()
        with open(path) as f:
            import json

            data = json.loads(f.readline())
        assert data["extra_field_1"] == "value1"
        assert data["extra_field_2"] == 42
