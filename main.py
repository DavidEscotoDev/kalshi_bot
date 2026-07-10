import logging
import os
import signal
import sys
import threading
import time
from decimal import Decimal

from config import Config
from data.database import (
    initialize_db,
    vacuum_database,
)
from market.order_book import LocalOrderBook
from market.websocket_client import KalshiWebSocketClient
from safety.kill_switch import KillSwitch
from strategy.macro_tracker import MacroTrackerStrategy

log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "logs"))
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "kalshi_bot.log")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ],
)

logger = logging.getLogger("kalshi_bot.main")

running = True
_shutdown_event = threading.Event()


def handle_shutdown(signum: int, frame: object | None) -> None:
    global running
    logger.info(f"Signal {signum} received. Initiating graceful shutdown...")
    running = False
    _shutdown_event.set()


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def run_loop() -> None:
    global running

    initialize_db()

    try:
        Config.validate()
        logger.info(
            f"Configuration verified. ENV: '{Config.ENV}', "
            f"SHADOW_MODE: '{Config.SHADOW_MODE}'"
        )
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        if os.getenv("KALSHI_TESTING") != "1":
            sys.exit(1)

    tickers_raw = os.getenv("KALSHI_TICKERS", os.getenv("KALSHI_TICKER", "FED-24DEC-T4.00"))
    ticker_configs = []
    for entry in tickers_raw.split(";"):
        parts = entry.split(",")
        t = parts[0].strip()
        s = parts[1].strip() if len(parts) > 1 else os.getenv("KALSHI_SECTOR", "Economics")
        ind_raw = parts[2].strip() if len(parts) > 2 else os.getenv("MACRO_INDICATOR", "CPI")
        i_list = [x.strip().upper() for x in ind_raw.replace("+", ",").split(",")]
        ticker_configs.append((t, s, i_list[0], i_list))

    indicator = ticker_configs[0][2]
    poll_interval = int(os.getenv("POLL_INTERVAL_SEC", "60"))
    vacuum_interval = int(os.getenv("VACUUM_INTERVAL_SEC", "86400"))

    if not Config.SHADOW_MODE and not os.getenv("LIVE_TRADE_CONFIRMED"):
        logger.critical(
            "LIVE MODE REQUIRES CONFIRMATION. Set LIVE_TRADE_CONFIRMED=1 in .env to proceed. "
            "Start in SHADOW_MODE=True first, verify logs, then add LIVE_TRADE_CONFIRMED=1."
        )
        sys.exit(1)

    logger.info(
        f"Starting Continuous Trading Loop. Tickers: {[t for t, _, _, _ in ticker_configs]}, "
        f"Indicator: {indicator}"
    )

    order_book = LocalOrderBook()
    kill_switch = KillSwitch()
    strategies = [
        MacroTrackerStrategy(ticker=t, sector=s, indicator=i, indicators=inds)
        for t, s, i, inds in ticker_configs
    ]

    ticker_to_sector = {t: s for t, s, _, _ in ticker_configs}
    for strat in strategies:
        strat.order_book = order_book
        strat.set_ticker_to_sector(ticker_to_sector)

    ws_client = KalshiWebSocketClient(ticker_configs[0][0], order_book)
    ws_client.connect()

    if not ws_client.wait_for_connection(timeout=10.0):
        logger.error(
            "WebSocket did not establish a valid connection within timeout. "
            "Please verify credentials, endpoint settings, and network access."
        )
        ws_client.disconnect()
        sys.exit(1)

    logger.info("Bot components successfully initialized and monitoring...")

    last_vacuum_time = 0.0

    while running:
        try:
            kill_switch_active, capital = kill_switch.check_and_trigger_with_capital()
            if kill_switch_active:
                logger.critical(
                    "Kill Switch safety triggered! Severing connections and halting bot."
                )
                break

            for strategy in strategies:
                try:
                    triggered = strategy.check_for_new_release(capital, Decimal("0.00"))
                    if triggered:
                        logger.info(f"Strategy {strategy.ticker} triggered: trade executed.")
                except Exception as e:
                    logger.error(f"Strategy {strategy.ticker} check failed: {e}")

            best_bid_price, _ = order_book.get_best_yes_bid()
            best_ask_price, _ = order_book.get_best_yes_ask()
            mid_price = (
                (best_bid_price + best_ask_price) / Decimal("2.0")
                if (best_bid_price and best_ask_price)
                else None
            )

            now = time.time()
            if now - last_vacuum_time >= vacuum_interval:
                try:
                    vacuum_database()
                except Exception as e:
                    logger.warning(f"Database vacuum failed: {e}")
                last_vacuum_time = now

            total_fees = sum(strat.execution_engine.fee_accumulator for strat in strategies)
            logger.info(
                f"[STATUS] Balance: ${capital:.2f} | "
                f"YES Bid: ${best_bid_price if best_bid_price else 'N/A'} | "
                f"YES Ask: ${best_ask_price if best_ask_price else 'N/A'} | "
                f"Mid: ${mid_price if mid_price else 'N/A'} | "
                f"Fee Accum: ${total_fees:.6f}"
            )

        except Exception as e:
            logger.error(f"Error in main loop iteration: {e}")

        _shutdown_event.wait(timeout=poll_interval)
        if not running:
            break

    logger.info("Shutting down background connection channels...")
    ws_client.disconnect()
    logger.info("Shutdown sequence finalized.")


if __name__ == "__main__":
    run_loop()
