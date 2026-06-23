#!/usr/bin/env python3
import argparse
import logging
import re
import sys
import time
from decimal import Decimal

from config import Config
from data.database import get_strategy_performance_summary, initialize_db
from execution.engine import ExecutionEngine
from market.order_book import LocalOrderBook
from market.websocket_client import KalshiWebSocketClient
from safety.kill_switch import KillSwitch
from safety.risk_manager import RiskManager
from strategy.macro_tracker import MockCalendarProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kalshi_bot.cli")

_TICKER_RE = re.compile(r"^[A-Z0-9\-]{3,50}$")


def validate_ticker(ticker: str) -> str:
    ticker = ticker.strip().upper()
    if not _TICKER_RE.match(ticker):
        raise ValueError(
            f"Invalid ticker format: {ticker}. Use uppercase alphanumeric with hyphens."
        )
    return ticker


def validate_sector(sector: str) -> str:
    sector = sector.strip()
    if not sector or len(sector) > 100:
        raise ValueError("Sector must be between 1 and 100 characters.")
    return sector


def run_kill_switch() -> None:
    print("\n" + "=" * 50)
    print("      MANUAL KILL SWITCH INITIATION")
    print("=" * 50)
    try:
        ks = KillSwitch()
        cancelled = ks.cancel_all_orders()
        print(f"Success: Instantly cancelled {cancelled} open orders.")
    except Exception as e:
        print(f"Error executing kill switch: {e}", file=sys.stderr)
        sys.exit(1)
    print("=" * 50 + "\n")


def view_order_book(ticker: str) -> None:
    ticker = validate_ticker(ticker)
    print("\n" + "=" * 50)
    print(f"  CONNECTING TO REAL-TIME ORDER BOOK: {ticker}")
    print("=" * 50)

    order_book = LocalOrderBook()

    def display_book(book: LocalOrderBook) -> None:
        print("\n\033[H\033[J")
        print("=" * 60)
        print(f" ORDER BOOK FOR: {ticker}")
        print("=" * 60)

        yes_bids = book.get_yes_bids()
        yes_asks = book.get_yes_asks()

        print("\n--- YES CONTRACT BOOK ---")
        print(f"{'Bid Qty':>12} | {'Bid Price':>10} || {'Ask Price':>10} | {'Ask Qty':>12}")
        print("-" * 60)

        max_rows = min(10, max(len(yes_bids), len(yes_asks)))
        for i in range(max_rows):
            bid_str = ""
            if i < len(yes_bids):
                price, qty = yes_bids[i]
                bid_str = f"{qty:>12.2f} | ${price:>9.4f}"
            else:
                bid_str = f"{'':>12} | {'':>10}"

            ask_str = ""
            if i < len(yes_asks):
                price, qty = yes_asks[i]
                ask_str = f"${price:>9.4f} | {qty:>12.2f}"
            else:
                ask_str = f"{'':>10} | {'':>12}"

            print(f"{bid_str} || {ask_str}")

        best_yes_bid, _ = book.get_best_yes_bid()
        best_yes_ask, _ = book.get_best_yes_ask()
        print("-" * 60)
        print(
            f"Best YES Bid: ${best_yes_bid if best_yes_bid else 'N/A'} | "
            f"Best YES Ask: ${best_yes_ask if best_yes_ask else 'N/A'}"
        )
        print("=" * 60)
        print("\nPress Ctrl+C to exit.")

    client = KalshiWebSocketClient(ticker, order_book, on_update_cb=display_book)
    client.connect()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDisconnecting...")
        client.disconnect()
        print("Done.")


def test_diagnostics() -> None:
    print("\n" + "=" * 50)
    print("      SYSTEM DIAGNOSTICS & SIGNING CHECK")
    print("=" * 50)
    try:
        print("1. Loading configurations...")
        Config.validate()
        print(f"   Environment: {Config.ENV}")
        print(f"   REST URL: {Config.get_rest_url()}")
        print(f"   WS URL: {Config.get_ws_url()}")

        print("\n2. Verification of Private RSA Key Loading...")
        Config.get_private_key()
        print("   RSA Private Key successfully parsed.")

        print("\n3. Testing REST API connection and signature authentication...")
        ks = KillSwitch()
        balance = ks.get_balance()
        print(f"   Authentication Succeeded! Available Balance: ${balance:.2f}")

        print("\n4. Checking Risk constraints...")
        print(f"   Max Position VaR Cap: {Config.MAX_VAR_LIMIT_PCT * 100}%")
        print(f"   Max Sector Concentration Cap: {Config.MAX_SECTOR_LIMIT_PCT * 100}%")
        print(f"   Kelly Fraction Multiplier: {Config.KELLY_MULTIPLIER}x")

        print("\nALL SYSTEMS OPERATIONAL!")
    except Exception as e:
        print(f"\nDiagnostic failed: {e}", file=sys.stderr)
        sys.exit(1)
    print("=" * 50 + "\n")


def view_historical_cutoff() -> None:
    print("\n" + "=" * 50)
    print("       HISTORICAL DATA CUTOFF TIMESTAMPS")
    print("=" * 50)
    try:
        engine = ExecutionEngine()
        path = "/trade-api/v2/historical/cutoff"
        url = f"{engine.base_url}{path}"
        headers = engine.sign_headers("GET", path)

        session = Config.get_verified_session()
        response = Config.request_with_retry(
            method="GET",
            url=url,
            headers=headers,
            session=session,
            timeout=Config.REQUEST_TIMEOUT_SEC,
        )
        if response.status_code == 200:
            data = response.json()
            print(f"Market Settled Cutoff:   {data.get('market_settled_ts')}")
            print(f"Trades Created Cutoff:   {data.get('trades_created_ts')}")
            print(f"Orders Updated Cutoff:   {data.get('orders_updated_ts')}")
        else:
            print(f"API Error ({response.status_code}): {response.text}", file=sys.stderr)
    except Exception as e:
        print(f"Error fetching historical cutoff: {e}", file=sys.stderr)
    print("=" * 50 + "\n")


def trigger_macro_release(
    indicator: str,
    actual: float,
    forecast: float,
    previous: float,
    ticker: str,
    sector: str,
) -> None:
    ticker = validate_ticker(ticker)
    sector = validate_sector(sector)

    print("\n" + "=" * 50)
    print("      TRIGGERING ECONOMIC RELEASE TRIGGER")
    print("=" * 50)
    try:
        risk_manager = RiskManager()
        execution_engine = ExecutionEngine()
        order_book = LocalOrderBook()

        try:
            ks = KillSwitch()
            total_capital = ks.get_balance()
        except Exception:
            total_capital = Decimal("10000.00")

        print(f"Account Balance / Capital: ${total_capital:.2f}")

        result = MockCalendarProvider.trigger_mock_release(
            indicator=indicator,
            actual=actual,
            forecast=forecast,
            previous=previous,
            ticker=ticker,
            sector=sector,
            risk_manager=risk_manager,
            execution_engine=execution_engine,
            order_book=order_book,
            total_capital=total_capital,
        )
        print(f"Execution Trigger Result: {result}")
    except Exception as e:
        print(f"Error triggering release: {e}", file=sys.stderr)
    print("=" * 50 + "\n")


def show_performance_report(indicator: str | None = None) -> None:
    print("\n" + "=" * 65)
    print("      STRATEGY PERFORMANCE REPORT")
    print("=" * 65)
    rows = get_strategy_performance_summary(indicator)
    if not rows:
        print("No performance data available yet.")
        print("=" * 65 + "\n")
        return

    header = (
        f"{'Indicator':<14} {'Signals':>8} {'Wins':>6} {'Losses':>7} "
        f"{'Win Rate':>9}   {'Avg Sigma':>7} {'Avg Conv':>9} {'Total Wager':>12}"
    )
    print(header)
    print("-" * 65)

    totals = {"signals": 0, "wins": 0, "losses": 0}
    for r in rows:
        total = r["total_signals"]
        wins = r["wins"]
        losses = r["losses"]
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
        totals["signals"] += total
        totals["wins"] += wins
        totals["losses"] += losses
        print(
            f"{r['indicator']:<14} {total:>8} {wins:>6} {losses:>7} "
            f"{win_rate:>7.1f}%   {r['avg_sigma']:>7} {r['avg_conviction']:>9} "
            f"${r['total_wager']:>10,.2f}"
        )

    print("-" * 65)
    t = totals
    tr = (t["wins"] / (t["wins"] + t["losses"]) * 100) if (t["wins"] + t["losses"]) > 0 else 0.0
    print(
        f"{'TOTAL':<14} {t['signals']:>8} {t['wins']:>6} {t['losses']:>7} "
        f"{tr:>7.1f}%"
    )
    print("=" * 65 + "\n")


def run_trading_bot(ticker: str, sector: str, sim_prob: float) -> None:
    ticker = validate_ticker(ticker)
    sector = validate_sector(sector)

    logger.info(f"Initializing Trading Bot for {ticker} in sector: {sector}")

    order_book = LocalOrderBook()
    risk_manager = RiskManager()
    execution_engine = ExecutionEngine()
    kill_switch = KillSwitch()

    sim_probability = Decimal(str(sim_prob))

    client = KalshiWebSocketClient(ticker, order_book)
    client.connect()

    logger.info("Waiting for WebSocket feed connection...")
    time.sleep(2.0)

    try:
        while True:
            logger.info("Running safety checks...")
            kill_active, balance = kill_switch.check_and_trigger_with_capital()
            if kill_active:
                logger.critical("Kill switch triggered! Halting trading bot.")
                break

            best_bid_price, best_bid_qty = order_book.get_best_yes_bid()
            best_ask_price, best_ask_qty = order_book.get_best_yes_ask()

            if not best_bid_price or not best_ask_price:
                logger.warning(
                    "Order book not fully synchronized yet. Skipping decision iteration..."
                )
                time.sleep(5)
                continue

            mid_price = (best_bid_price + best_ask_price) / Decimal("2.0")
            logger.info(
                f"Market Status - Best Bid: ${best_bid_price:.4f}, "
                f"Best Ask: ${best_ask_price:.4f}, Mid: ${mid_price:.4f}"
            )

            total_capital = balance
            current_sector_exposure = Decimal("0.0")

            if sim_probability > best_ask_price:
                price_to_buy = best_ask_price
                wager = risk_manager.size_order(
                    sim_probability,
                    price_to_buy,
                    "yes",
                    sector,
                    current_sector_exposure,
                    total_capital,
                )
                if wager > 0:
                    quantity = wager / price_to_buy
                    logger.info(
                        f"SIGNAL DETECTED: BUY YES at ${price_to_buy:.4f}. "
                        f"Suggested Wager: ${wager:.2f} ({quantity:.2f} contracts)"
                    )
                    payload = execution_engine.format_price(price_to_buy)
                    qty_payload = execution_engine.format_quantity(quantity)
                    logger.info(
                        f"[SIMULATED EXECUTION] Sending JSON -> "
                        f"Price: '{payload}', Count: '{qty_payload}'"
                    )
                    execution_engine.handle_order_fill(price_to_buy, quantity, is_buy=True)
            elif sim_probability < best_bid_price:
                no_price = Decimal("1.0000") - best_bid_price
                wager = risk_manager.size_order(
                    sim_probability,
                    best_bid_price,
                    "no",
                    sector,
                    current_sector_exposure,
                    total_capital,
                )
                if wager > 0:
                    quantity = wager / no_price
                    logger.info(
                        f"SIGNAL DETECTED: BUY NO at ${no_price:.4f}. "
                        f"Suggested Wager: ${wager:.2f} ({quantity:.2f} contracts)"
                    )
                    payload = execution_engine.format_price(no_price)
                    qty_payload = execution_engine.format_quantity(quantity)
                    logger.info(
                        f"[SIMULATED EXECUTION] Sending JSON -> "
                        f"Price: '{payload}', Count: '{qty_payload}'"
                    )
                    execution_engine.handle_order_fill(no_price, quantity, is_buy=True)
            else:
                logger.info("Estimated probability is within bid-ask spread. No trade action.")

            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down bot...")
    finally:
        client.disconnect()
        logger.info("Bot shutdown complete.")


def main() -> None:
    initialize_db()

    parser = argparse.ArgumentParser(
        description="Kalshi Prediction Market Algorithmic Trading System CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="System sub-commands")

    run_parser = subparsers.add_parser("run-bot", help="Run the algorithmic trading bot")
    run_parser.add_argument(
        "--ticker", required=True, help="Kalshi market ticker (e.g. FED-24DEC-T4.00)"
    )
    run_parser.add_argument("--sector", required=True, help="Concentration sector (e.g. Economics)")
    run_parser.add_argument(
        "--prob",
        type=float,
        default=0.65,
        help="Estimated YES probability (0.0 to 1.0)",
    )

    subparsers.add_parser(
        "kill-switch", help="Trigger safety kill switch to cancel all open orders"
    )

    view_parser = subparsers.add_parser(
        "view-book", help="View real-time order book sync for a ticker"
    )
    view_parser.add_argument("--ticker", required=True, help="Kalshi market ticker")

    subparsers.add_parser(
        "test-diagnostics", help="Run client diagnostics and authentication tests"
    )

    subparsers.add_parser("view-cutoff", help="Retrieve historical data cutoff timestamps")

    mock_parser = subparsers.add_parser(
        "trigger-mock-release", help="Trigger a mock macroeconomic release to trade on"
    )
    mock_parser.add_argument(
        "--indicator",
        required=True,
        choices=["FOMC", "CPI", "PCE"],
        help="Macro indicator type",
    )
    mock_parser.add_argument("--actual", type=float, required=True, help="Actual indicator value")
    mock_parser.add_argument(
        "--forecast", type=float, required=True, help="Forecasted indicator value"
    )
    mock_parser.add_argument("--previous", type=float, default=0.0, help="Previous indicator value")
    mock_parser.add_argument(
        "--ticker", default="FED-MOCK", help="Simulated market ticker to target"
    )
    mock_parser.add_argument("--sector", default="Economics", help="Concentration sector")

    perf_parser = subparsers.add_parser(
        "perf-report", help="Show strategy performance summary from database"
    )
    perf_parser.add_argument(
        "--indicator",
        choices=["CPI", "PCE", "FOMC"],
        help="Filter by indicator",
    )

    args = parser.parse_args()

    if args.command == "run-bot":
        run_trading_bot(args.ticker, args.sector, args.prob)
    elif args.command == "kill-switch":
        run_kill_switch()
    elif args.command == "view-book":
        view_order_book(args.ticker)
    elif args.command == "test-diagnostics":
        test_diagnostics()
    elif args.command == "view-cutoff":
        view_historical_cutoff()
    elif args.command == "trigger-mock-release":
        trigger_macro_release(
            indicator=args.indicator,
            actual=args.actual,
            forecast=args.forecast,
            previous=args.previous,
            ticker=args.ticker,
            sector=args.sector,
        )
    elif args.command == "perf-report":
        show_performance_report(indicator=args.indicator)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
