# Contributing

Thank you for your interest in contributing! This project demonstrates production-grade safety patterns for autonomous trading systems. Contributions that improve reliability, observability, or safety are especially welcome.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Git
- Kalshi demo account (for testing)

### Setup
```bash
git clone https://github.com/DavidEscotoDev/kalshi_bot.git
cd kalshi_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-cov pytest-asyncio hypothesis ruff mypy
cp .env.example .env
# Edit .env with KALSHI_TESTING=1 for test runs
```

### Run Tests
```bash
# All tests with coverage
pytest tests/ -v --cov=. --cov-report=term-missing --cov-fail-under=85

# Specific module
pytest tests/test_kill_switch.py -v

# Lint & type-check
ruff check .
mypy .
```

---

## Development Workflow

### 1. Fork & Branch
```bash
git checkout -b feat/your-feature-name
# or fix/bug-description, docs/..., refactor/...
```

### 2. Make Changes
- Follow code style (Black, Ruff, type hints)
- Add tests for new functionality
- Update docs if behavior changes

### 3. Commit Messages
Use **Conventional Commits**:
```
type(scope): brief description

Longer explanation if needed.

Closes #123
```

| Type | Use For |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code restructure, no behavior change |
| `test` | Adding/updating tests |
| `chore` | Maintenance (deps, config, CI) |
| `perf` | Performance improvement |
| `security` | Security fix |

**Examples**:
```
feat(safety): add circuit breaker state persistence

Persists OPEN/HALF_OPEN state to SQLite so restarts don't reset breaker.
Closes #45

fix(execution): handle Kalshi API 429 with retry-after header

Previously used fixed backoff; now respects server's Retry-After.
```

### 4. Push & PR
```bash
git push origin feat/your-feature-name
# Open PR on GitHub
```

---

## Code Standards

### Formatting & Linting
```bash
# Auto-format
ruff check --fix .
# or
black .

# Lint only
ruff check .
mypy .
```

**Configuration**: `pyproject.toml` defines:
- Line length: 100
- Target: Python 3.12
- Quote style: double
- Select: E, F, W, I, N, UP, S, B, A, C4, SIM
- Ignore: S101 (assert in tests)

### Type Hints
- **Required**: Function signatures (args + return)
- **Optional**: Local variables (infer where clear)
- **Style**: `from __future__ import annotations` at top of every file

```python
# Good
def calculate_kelly(estimated_prob: Decimal, market_price: Decimal, side: str) -> Decimal:
    ...

# Acceptable (inferred)
raw_kelly = self.calculate_kelly_fraction(p, P, side)
```

### Imports
```python
# Standard library first
import logging
import os
from decimal import Decimal
from typing import Any

# Third-party
import requests
from cryptography.hazmat.primitives import hashes

# Local
from config import Config
from market.order_book import LocalOrderBook
```

---

## Testing Requirements

### Coverage Targets
| Module | Minimum Coverage |
|--------|-----------------|
| `safety/*` | 95% |
| `config.py` | 90% |
| `execution/engine.py` | 85% |
| `market/websocket_client.py` | 80% |
| `strategy/*` | 70% |
| **Overall** | **85%** |

### Test Structure
```
tests/
├── test_config.py           # Config validation, URL building, key loading
├── test_kill_switch.py      # Balance checks, cancel_all, fail-closed
├── test_risk_manager.py     # Kelly math, VaR cap, sector cap
├── test_websocket.py        # Reconnect, gap detection, oversized msg
├── test_execution.py        # Duplicate detection, shadow vs live, fees
└── conftest.py              # Fixtures, mock setup
```

### Writing Tests
```python
# tests/test_risk_manager.py
import pytest
from decimal import Decimal
from safety.risk_manager import RiskManager

class TestRiskManager:
    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_kelly_yes_positive_edge(self, rm):
        """Kelly > 0 when p > P for YES."""
        result = rm.calculate_kelly_fraction(Decimal("0.6"), Decimal("0.5"), "yes")
        assert result > Decimal("0")

    def test_kelly_no_positive_edge(self, rm):
        """Kelly > 0 when P > p for NO (i.e., p < P)."""
        result = rm.calculate_kelly_fraction(Decimal("0.4"), Decimal("0.5"), "no")
        assert result > Decimal("0")

    def test_kelly_zero_on_no_edge(self, rm):
        """No edge → zero fraction."""
        assert rm.calculate_kelly_fraction(Decimal("0.5"), Decimal("0.5"), "yes") == Decimal("0")

    @pytest.mark.parametrize("side", ["yes", "no"])
    def test_fractional_kelly_capped_at_var(self, rm, side):
        """Final fraction never exceeds VaR limit (2%)."""
        # High edge → raw Kelly > 0.02
        p = Decimal("0.9") if side == "yes" else Decimal("0.1")
        P = Decimal("0.5")
        fraction = rm.get_position_size_fraction(p, P, side)
        assert fraction <= Decimal("0.02")
```

### Mocking External Dependencies
```python
# conftest.py
import pytest
from unittest.mock import Mock, patch

@pytest.fixture
def mock_kalshi_api():
    with patch("config.Config.get_verified_session") as mock_session:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"balance_dollars": "1000.00"}
        mock_session.return_value.request.return_value = mock_resp
        yield mock_session
```

### Hypothesis (Property-Based Testing)
```python
# tests/test_risk_manager.py
from hypothesis import given, strategies as st
from decimal import Decimal

@given(
    p=st.decimals(min_value="0", max_value="1", places=4),
    P=st.decimals(min_value="0.01", max_value="0.99", places=4),
)
def test_kelly_bounds(p: Decimal, P: Decimal):
    """Kelly fraction always in [-1, 1] for valid probabilities."""
    rm = RiskManager()
    try:
        result = rm.calculate_kelly_fraction(p, P, "yes")
        assert Decimal("-1") <= result <= Decimal("1")
    except ValueError:
        # Invalid p/P combinations raise — that's fine
        pass
```

---

## Safety-Critical Changes

Changes to these modules require **extra review**:
- `safety/kill_switch.py`
- `safety/risk_manager.py`
- `config.py` (validation logic)
- `execution/engine.py` (order placement)

**Requirements**:
1. At least 2 reviewers (one must be maintainer)
2. All existing tests pass + new tests for change
3. Shadow mode validation for 24h before merge (if behavior changes)
4. Update `ARCHITECTURE.md` if design changes

---

## Documentation

Update these when relevant:
- `README.md` — Quick start, config, architecture diagram
- `docs/ARCHITECTURE.md` — Design decisions, data flows
- `docs/DEPLOYMENT.md` — Deployment procedures
- `RUNBOOK.md` — Operational procedures

**Doc Style**:
- Markdown with proper headers
- Code blocks for all commands
- Tables for configuration/metrics
- Mermaid diagrams for architecture

---

## Dependency Management

### Adding Dependencies
```bash
# 1. Add to requirements.txt with pinned version
echo "new-package==1.2.3" >> requirements.txt

# 2. Install & test
pip install -r requirements.txt
pytest tests/

# 3. Update pyproject.toml if needed (ruff/mypy config)
```

### Security
```bash
# Audit before merge
pip-audit -r requirements.txt
# or
safety check -r requirements.txt
```

---

## Release Process

1. **Version bump** in `pyproject.toml` (`[project] version = "x.y.z"`)
2. **Changelog** update (manual or `git log --oneline --since="last tag"`)
3. **Tag**: `git tag -a v1.2.3 -m "Release v1.2.3"`
4. **Push**: `git push origin v1.2.3`
5. **GitHub Actions** builds & publishes (if configured)

---

## Code of Conduct

- Be respectful and inclusive
- Focus on technical merit
- No tolerance for harassment
- Report issues to maintainers privately

---

## Questions?

- Open a GitHub Discussion for design questions
- Open an Issue for bugs or feature requests
- Check existing Issues/PRs before starting work

---

## Recognition

Contributors will be acknowledged in:
- Release notes
- README contributors section (optional)
- Git history (immutable)

Thank you for making autonomous systems safer!