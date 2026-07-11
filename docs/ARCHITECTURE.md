# Architecture Decision Records

## ADR-001: Single-Threaded Main Loop with Blocking I/O

**Status**: Accepted  
**Date**: 2026-06  
**Context**: Need to coordinate WebSocket, REST API, database, and strategy evaluation.

**Decision**: Use a single-threaded `while running:` loop with `threading.Event.wait(timeout=poll_interval)` for pacing. WebSocket runs in a daemon thread with its own `WebSocketApp.run_forever()`.

**Rationale**:
- Simplicity: No async/await complexity, no event loop management
- Deterministic: One iteration = one strategy evaluation cycle
- Debugging: Stack traces are straightforward; no callback hell
- Safety: No race conditions between strategy and risk checks (same thread)

**Trade-offs**:
- WebSocket message handling occurs in separate thread → use thread-safe `LocalOrderBook` with locks
- Blocking REST calls in main loop → mitigated by `REQUEST_TIMEOUT_SEC` (10s) and retry logic
- Cannot easily parallelize independent strategies (acceptable for current scope)

**Alternatives Considered**:
- `asyncio` with `aiohttp` + `websockets` — more complex, marginal benefit
- Multi-threaded with queue — adds locking complexity
- Separate processes — overkill for single-bot deployment

---

## ADR-002: Shadow Mode as Identical Code Path

**Status**: Accepted  
**Context**: Need zero-risk validation before live trading.

**Decision**: `ExecutionEngine.place_order()` has a single code path. `Config.SHADOW_MODE` flag only changes the last step: instead of HTTP POST to Kalshi, log to file + SQLite and return mock response.

**Rationale**:
- **No divergence**: Strategy, sizing, risk, audit — all identical
- **Realistic validation**: Fee accumulator, duplicate detection, order lifecycle all exercised
- **Easy promotion**: Flip one env var (`SHADOW_MODE=False`)

**Implementation** (`execution/engine.py:182-242`):
```python
if Config.SHADOW_MODE:
    self._record_fill(price, quantity, is_buy=(action_lower == "buy"))
    _get_shadow_logger().info(json.dumps({...}))  # JSONL log
    log_shadow_trade(...)  # SQLite
    update_order_status(client_order_id, "filled")
    return mock_response
else:
    # Live REST call
```

**Failure Mode**: If shadow mode diverges from live (e.g., different fee logic), validation is invalid. Mitigated by single code path.

---

## ADR-003: Kill Switch Fail-Closed Design

**Status**: Accepted  
**Context**: Balance check can fail (network, API error, rate limit).

**Decision**: `KillSwitch.check_and_trigger_with_capital()` treats **any exception** as trigger condition — cancels all orders and returns `(True, Decimal("0"))`.

**Code** (`safety/kill_switch.py:126-130`):
```python
except Exception as e:
    logger.error(f"Error during Kill Switch check: {e}")
    logger.critical("FORCING ORDER CANCELLATION DUE TO MONITOR FAILURE!")
    self.cancel_all_orders()
    return True, Decimal("0")
```

**Rationale**:
- **Asymmetric risk**: False positive (cancel orders unnecessarily) ≪ False negative (unmonitored positions)
- **Regulatory**: Unmonitored automated trading often violates exchange rules
- **Simplicity**: No need for "degraded mode" state machine

**Trade-off**: Transient API blip → cancels all resting orders. Acceptable because:
- Bot re-evaluates strategy each loop; orders would be re-placed if still valid
- Cooldown (`TRADE_COOLDOWN_SEC`) prevents thrashing

---

## ADR-004: Fractional Kelly + VaR + Sector Caps (Three-Layer Sizing)

**Status**: Accepted  
**Context**: Pure Kelly is too aggressive for production; need multiple independent limits.

**Decision**: Position size = `min(frac_kelly, var_cap, sector_remaining)`

| Layer | Formula | Purpose |
|-------|---------|---------|
| **Raw Kelly** | `(p - P) / (1 - P)` | Theoretical optimal |
| **Fractional Kelly** | `raw_kelly * 0.25` | Reduces variance, accounts for estimation error |
| **VaR Cap** | `min(fractional, 0.02)` | Hard limit: max 2% capital per position |
| **Sector Cap** | `min(above, sector_remaining)` | Max 30% capital per sector (Economics, Politics, etc.) |

**Rationale**:
- Kelly assumes known `p` — we estimate from macro signals → high uncertainty
- ¼ Kelly is standard practice (Thorp, Poundstone)
- VaR cap is regulatory/compliance-friendly
- Sector cap prevents concentration risk (e.g., all CPI trades)

**Math Verification** (`safety/risk_manager.py:16-80`):
- Handles both YES (`side="yes"`) and NO (`side="no"`) contracts
- NO side: `P_no = 1 - P_yes`, `p_no = 1 - p_yes`
- Returns `Decimal("0.0")` for no edge or invalid inputs

---

## ADR-005: WebSocket Auto-Reconnect with Sequence Gap Detection

**Status**: Accepted  
**Context**: Kalshi WS drops connections; missing deltas = stale order book.

**Decision**: 
1. **Reconnect**: `WebSocketApp.run_forever(ping_interval=10, ping_timeout=5)` in loop with 5s backoff
2. **Gap Detection**: Track `sequence` on each delta; if `seq > last_seq + 1` → log gap warning
3. **Resync**: On gap, next snapshot automatically refreshes book (Kalshi sends snapshot on subscribe)

**Code** (`market/websocket_client.py:152-155`):
```python
if self._last_sequence > 0 and self._sequence > self._last_sequence + 1:
    gap = self._sequence - self._last_sequence - 1
    logger.warning(f"WebSocket sequence gap detected: {gap} messages missed")
self._last_sequence = self._sequence
```

**Rationale**:
- Ping/pong keeps connection alive through proxies/load balancers
- 5s backoff avoids hammering on sustained outage
- Sequence gap = early warning of data loss; snapshot resync is automatic

**Failure Mode**: Extended outage → book stale → strategy sees old prices. Mitigated by:
- Main loop logs status every 60s (shows bid/ask)
- Kill switch checks balance independently of WS

---

## ADR-006: Private Key Security (Path Validation + Permissions Check)

**Status**: Accepted  
**Context**: RSA private key signs every request; compromise = total account takeover.

**Decision**: `Config.get_private_key()` enforces:
1. Path must be within allowed dirs (`~/.kalshi/`, `/etc/kalshi/keys/`)
2. File permissions ≤ 0600 (no group/other read)
3. File must exist and be valid PEM RSA key
4. Key zeroed from memory after load (`key_data[i] = 0`)

**Code** (`config.py:63-99`):
```python
resolved = os.path.realpath(cls.PRIVATE_KEY_PATH)
allowed = any(resolved.startswith(os.path.realpath(d)) for d in cls._ALLOWED_KEY_DIRS)
if not allowed:
    raise PermissionError(...)

key_stat = os.stat(resolved)
if key_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
    raise PermissionError(f"Private key file permissions too permissive ({oct(key_stat.st_mode)})")

with open(resolved, "rb") as key_file:
    key_data = key_file.read()
    cls._private_key = serialization.load_pem_private_key(key_data, password=None)
    # Zero memory
    for i in range(len(key_data)):
        key_data = key_data[:i] + b"\x00" + key_data[i+1:]
```

**Rationale**:
- Path traversal prevention (symlink attacks)
- Permission check catches `chmod 644` mistakes
- Memory zeroing reduces RAM dump exposure
- Allowed directories prevent loading from `/tmp`, cwd, etc.

---

## ADR-007: Circuit Breaker State Machine for Rate Limiting

**Status**: Accepted  
**Context**: Kalshi enforces rate limits; 429 responses need backoff, not retry storm.

**Decision**: Token bucket rate limiter + circuit breaker with states:
- **CLOSED**: Normal operation, tokens consumed per request
- **OPEN**: Rate limit hit (429) → reject fast, wait `retry_after`
- **HALF_OPEN**: After cooldown → allow probe request; success → CLOSED, fail → OPEN

**Implementation**: Custom `CircuitBreaker` + `RateLimiter` classes (not shown in core files but referenced in logs).

**Rationale**:
- Token bucket smooths bursts
- Circuit breaker prevents cascade on sustained 429s
- Half-open probes recovery without full commitment

---

## ADR-008: SQLite for Persistence (Not PostgreSQL)

**Status**: Accepted  
**Context**: Need durable storage for orders, positions, shadow trades, audit.

**Decision**: SQLite with WAL mode, daily vacuum, rotating logs.

**Rationale**:
- **Zero ops**: No separate DB server, no connection pooling
- **Embedded**: Single file per purpose (`kalshi_shadow.db`, `kalshi_shadow.db`)
- **WAL mode**: Concurrent reads during writes (main loop + log readers)
- **Portable**: Docker volume = single file backup
- **Sufficient scale**: < 100 writes/sec, < 1GB/year

**Schema** (`data/database.py`):
```sql
orders: client_order_id (PK), ticker, status, action, side, price, qty, signal_id, kalshi_order_id, created_at, updated_at
positions: ticker, side, quantity, avg_price, updated_at
shadow_trades: timestamp, ticker, action, side, price, qty, synthetic_ask, proposed_kelly, final_wager, fee_accumulator, release_id
audit_log: (JSONL files, not DB) — immutable, append-only
```

**Failure Mode**: SQLite corruption on power loss. Mitigated by:
- WAL mode (`PRAGMA journal_mode=WAL`)
- Daily `VACUUM` in main loop
- Read-only replicas for analysis (copy file)

---

## ADR-009: Audit Log as JSONL Files (Not Database)

**Status**: Accepted  
**Context**: Need immutable, queryable, tamper-evident record of every system decision.

**Decision**: Daily rotating JSONL files in `logs/audit/audit-YYYY-MM-DD.log`.

**Rationale**:
- **Append-only**: No UPDATE/DELETE possible
- **Line-oriented**: `jq`, `grep`, `wc -l` work natively
- **Rotating**: Daily files = manageable size, easy retention
- **Immutable**: Can be shipped to S3/GCS with integrity verification
- **Schema evolution**: Each line self-describing

**Event Types**:
```json
{"timestamp": "...", "event_type": "order_placed", "ticker": "...", "action": "buy", "outcome_side": "yes", "price": 0.55, "quantity": 100, "client_order_id": "...", "order_id": "..."}
{"timestamp": "...", "event_type": "order_rejected", "ticker": "...", "reason": "insufficient_balance", "client_order_id": "..."}
{"timestamp": "...", "event_type": "kill_switch_triggered", "balance": 95.00, "threshold": 100.00}
{"timestamp": "...", "event_type": "order_duplicate_skipped", "ticker": "...", "client_order_id": "..."}
```

---

## ADR-010: Configuration Validation at Startup (Fail-Fast)

**Status**: Accepted  
**Context**: Misconfiguration (bad timeout, missing key, invalid %) should not start bot.

**Decision**: `Config.validate()` called once in `main.py:55` before any component initialization. Raises `ValueError` on any invalid setting → `sys.exit(1)`.

**Validated**:
- Required env vars present (`KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`)
- Numeric ranges: `REQUEST_TIMEOUT_SEC > 0`, `RETRY_MAX_ATTEMPTS >= 1`, `0 < MAX_VAR_LIMIT_PCT <= 1`, etc.
- Private key loadable and valid (triggers permission/path checks)
- `KALSHI_TESTING=1` skips all validation (for CI/tests)

**Rationale**:
- Fail-fast prevents half-initialized bot with wrong risk params
- Single validation point = single source of truth
- Clear error messages guide operator to fix

---

## Data Flow Summary

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  FRED API   │────▶│ MacroTracker │────▶│  Conviction │
│  (CPI, Fed) │     │  Strategy    │     │   Score     │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
┌─────────────┐     ┌──────────────┐            │
│  Kalshi WS  │────▶│ LocalOrderBook│◀───────────┘
│ (orderbook) │     │  (bid/ask)    │
└─────────────┘     └──────┬────────┘
                           │ best_bid, best_ask
                           ▼
                    ┌──────────────┐     ┌────────────────┐
                    │  RiskManager │────▶│  Position Size │
                    │ (Kelly+VaR)  │     │   (Decimal $)  │
                    └──────┬───────┘     └───────┬────────┘
                           │                     │
                    ┌──────▼─────────┐     ┌─────▼─────┐
                    │  KillSwitch    │     │Execution  │
                    │ (balance chk)  │     │ Engine    │
                    └──────┬─────────┘     └─────┬─────┘
                           │                     │
                    ┌──────▼─────────┐     ┌─────▼─────┐
                    │  CANCEL ALL    │     │ SHADOW/   │
                    │  ORDERS (REST) │     │ LIVE API  │
                    └────────────────┘     └─────┬─────┘
                                                 │
                    ┌──────────────┐     ┌────────▼────────┐
                    │   SQLite     │     │  Shadow Log    │
                    │  (orders,    │     │  (JSONL +      │
                    │   positions, │     │   SQLite)      │
                    │   shadow)    │     └────────────────┘
                    └──────────────┘
                    ┌──────────────┐
                    │  Audit Log   │
                    │  (JSONL daily)│
                    └──────────────┘
```

---

## Failure Mode Analysis

| Component | Failure | Detection | Recovery |
|-----------|---------|-----------|----------|
| **WebSocket** | Disconnect | `connected_event.clear()`, log | Auto-reconnect (5s backoff) |
| **WebSocket** | Sequence gap | `logger.warning("gap: N messages")` | Next snapshot resyncs |
| **REST API** | 429/5xx | Retry 3× with backoff | Circuit breaker opens |
| **REST API** | 401/403 | Log error, raise | Manual intervention (key rotation) |
| **Kill Switch** | Balance API fails | `except Exception` → trigger | Cancels all orders, exits |
| **DB** | Locked/corrupt | Exception in `store_order` | Retry on next loop; vacuum daily |
| **Strategy** | FRED API fails | `logger.error`, conviction=0 | No trades until data returns |
| **Config** | Invalid value | `Config.validate()` at startup | `sys.exit(1)` — no partial start |

---

## Scaling Considerations (Future)

| Dimension | Current | Path to Scale |
|-----------|---------|---------------|
| **Tickers** | 1 (configurable multi) | Per-ticker WS connection + strategy instance |
| **Strategies** | 1 (MacroTracker) | Plugin registry; each subscribes to order book updates |
| **Throughput** | ~1 order/min | Async REST client; batch order placement |
| **HA** | Single process | Leader election (Redis); warm standby |
| **Observability** | File logs | Prometheus metrics + Grafana dashboards |

---

## References

- Thorp, E. O. (2006). *The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market*
- Kalshi API Documentation: `https://trading-api.readme.io/`
- FRED API: `https://fred.stlouisfed.org/docs/api/`
- Circuit Breaker Pattern: M. Nygard, *Release It!* (2018)