#!/usr/bin/env python3
"""
bot.py — AlgoTrade MT5 Main Entry Point

A trading bot that connects to MetaTrader 5, runs a moving-average crossover
strategy, executes orders with risk management, and maintains shared state
for a web dashboard.

Usage:
    python bot.py [--config path/to/config.yaml]

If --config is omitted, config.yaml in the same directory is used.
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time
from typing import Optional

import yaml

# Local modules.
from mt5_connector import (
    get_account_info,
    get_current_spread,
    get_rates,
    initialize_mt5,
    is_market_open,
    is_mt5_available,
    shutdown_mt5,
)
from strategy import compute_signals, get_latest_signal
from executor import (
    calculate_position_size,
    calculate_sl_tp,
    close_all_positions,
    get_open_positions,
    place_order,
    positions_to_summary,
)
from state import BotState, get_global_state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "config.yaml")
SHUTDOWN_TIMEOUT = 10  # seconds to wait for clean shutdown


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def setup_logging(config: dict) -> logging.Logger:
    """Configure logging to both console and a rotating file."""
    log_cfg = config.get("logging", {})
    level_name = log_cfg.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any pre-existing handlers (idempotent).
    root.handlers.clear()

    formatter = logging.Formatter(fmt)

    # Console handler.
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (rotating).
    log_file = log_cfg.get("file", "logs/bot.log")
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    max_bytes = int(log_cfg.get("max_bytes", 10_485_760))
    backup_count = int(log_cfg.get("backup_count", 5))

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logger = logging.getLogger("bot")
    logger.info("Logging initialised (level=%s, file=%s).", level_name, log_file)
    return logger


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    """Load YAML configuration from *path*."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    if not config:
        raise ValueError("Config file is empty or invalid.")

    return config


# ---------------------------------------------------------------------------
# Shutdown handler
# ---------------------------------------------------------------------------
_shutdown_requested = False


def _handle_shutdown_signal(signum, frame):
    """Set the shutdown flag on SIGINT / SIGTERM."""
    global _shutdown_requested
    _shutdown_requested = True
    logging.getLogger("bot").info(
        "Received signal %s — graceful shutdown initiated.", signum
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_bot(config_path: str) -> None:
    """Run the trading bot until interrupted."""
    # --- Load config --------------------------------------------------------
    config = load_config(config_path)
    logger = setup_logging(config)
    logger.info("AlgoTrade MT5 starting up …")

    state: BotState = get_global_state()
    state.update_status("starting")

    # Shortcut references to nested config sections.
    symbol = config.get("symbol", "EURUSD")
    timeframe = config.get("timeframe", "M5")
    strat = config.get("strategy", {})
    risk = config.get("risk", {})
    bot_cfg = config.get("bot", {})
    order_cfg = config.get("order", {})

    fast_ma = int(strat.get("fast_ma_period", 10))
    slow_ma = int(strat.get("slow_ma_period", 30))
    min_bars = int(strat.get("min_bars", 50))
    risk_pct = float(risk.get("risk_percent", 1.0))
    sl_mult = float(risk.get("sl_atr_multiplier", 1.5))
    tp_mult = float(risk.get("tp_atr_multiplier", 3.0))
    max_spread = float(risk.get("max_spread_pips", 3.0))
    min_rr = float(risk.get("min_risk_reward", 1.5))
    check_interval = float(bot_cfg.get("check_interval_seconds", 60))
    demo_mode = bool(bot_cfg.get("demo_mode", True))
    close_on_shutdown = bool(bot_cfg.get("close_on_shutdown", False))
    state_export_interval = float(bot_cfg.get("state_export_interval_seconds", 5))
    magic = int(order_cfg.get("magic", 202401))

    logger.info("Symbol: %s  Timeframe: %s  Demo: %s", symbol, timeframe, demo_mode)
    logger.info(
        "Strategy: MA(%d, %d)  Risk: %.1f%%  SL-mult: %.1f  TP-mult: %.1f",
        fast_ma,
        slow_ma,
        risk_pct,
        sl_mult,
        tp_mult,
    )

    # --- Connect to MT5 ----------------------------------------------------
    if not is_mt5_available():
        state.add_error(
            "MetaTrader5 Python package not available — install MT5 terminal or "
            "run on Windows/Wine. Bot will continue in dry-run mode."
        )
        logger.warning("MT5 not available — running in DRY-RUN mode (no orders).")

    mt5_connected = initialize_mt5(config)

    if not mt5_connected:
        if not demo_mode:
            state.add_error("MT5 connection failed and demo_mode is OFF — aborting.")
            state.update_status("error")
            logger.critical("Cannot connect to MT5 in live mode. Exiting.")
            return
        # In demo mode we can still run the signal logic (just no orders).
        logger.warning("Running without MT5 — signals will be logged but no orders sent.")

    # --- Register signal handlers ------------------------------------------
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    # --- Main loop ---------------------------------------------------------
    state.update_status("running")
    last_export_time = 0.0
    iteration = 0

    try:
        while not _shutdown_requested:
            iteration += 1
            cycle_start = time.time()

            # 1. Fetch account info (every iteration for freshness).
            acct = get_account_info() if mt5_connected else None
            state.update_account(acct)
            balance = acct.get("balance", 0.0) if acct else 0.0

            # 2. Fetch rates.
            df = get_rates(symbol, timeframe, count=min_bars)

            if df is None or len(df) < slow_ma:
                logger.debug(
                    "Insufficient data (got %d bars, need %d). Skipping cycle.",
                    len(df) if df is not None else 0,
                    slow_ma,
                )
                _update_and_export(state, mt5_connected, magic, symbol, last_export_time, state_export_interval)
                _sleep_remaining(cycle_start, check_interval)
                continue

            # 3. Compute signals.
            df = compute_signals(df, fast_period=fast_ma, slow_period=slow_ma)
            signal_str = get_latest_signal(df)
            state.update_signal(signal_str)

            # 4. Get open positions for *this* symbol + magic.
            open_positions = get_open_positions(symbol=symbol, magic=magic)
            total_pnl = sum(p.get("profit", 0.0) for p in open_positions)
            state.update_positions(positions_to_summary(open_positions), pnl=total_pnl)

            logger.debug(
                "[cycle %d] signal=%s  positions=%d  pnl=%.2f",
                iteration,
                signal_str,
                len(open_positions),
                total_pnl,
            )

            # 5. Market-open check.
            if not is_market_open(symbol):
                logger.debug("Market closed for %s — skipping trade logic.", symbol)
                _update_and_export(state, mt5_connected, magic, symbol, last_export_time, state_export_interval)
                _sleep_remaining(cycle_start, check_interval)
                continue

            # 6. Trade logic.
            if signal_str == "HOLD":
                # No action needed.
                pass
            elif len(open_positions) == 0:
                # No position yet — try to enter.
                _try_enter(
                    config, df, signal_str, symbol, balance, max_spread,
                    sl_mult, tp_mult, risk_pct, min_rr, magic,
                    mt5_connected, demo_mode, state,
                )
            else:
                # We have an existing position. Check if signal reversed.
                _handle_existing(
                    config, df, signal_str, open_positions, symbol,
                    sl_mult, tp_mult, risk_pct, min_rr, max_spread,
                    balance, magic, mt5_connected, demo_mode, state,
                )

            # 7. Export state for dashboard.
            last_export_time = _update_and_export(
                state, mt5_connected, magic, symbol,
                last_export_time, state_export_interval,
            )

            # 8. Sleep for the remainder of the cycle.
            _sleep_remaining(cycle_start, check_interval)

    except Exception:
        logger.exception("Unhandled exception in main loop.")
        state.update_status("error")
        state.add_error("Unhandled exception — see logs for traceback.")
    finally:
        state.update_status("stopped")
        logger.info("Bot shutting down …")

        if close_on_shutdown and mt5_connected:
            logger.info("Closing all positions (close_on_shutdown=true).")
            close_all_positions(symbol=symbol, magic=magic, config=config)

        shutdown_mt5()
        state.export_to_file()
        logger.info("AlgoTrade MT5 stopped.")


# ---------------------------------------------------------------------------
# Trade helpers
# ---------------------------------------------------------------------------


def _try_enter(
    config: dict,
    df,
    signal_str: str,
    symbol: str,
    balance: float,
    max_spread: float,
    sl_mult: float,
    tp_mult: float,
    risk_pct: float,
    min_rr: float,
    magic: int,
    mt5_connected: bool,
    demo_mode: bool,
    state: BotState,
) -> None:
    """Attempt to enter a new position based on *signal_str*."""
    logger_ = logging.getLogger("bot")

    # Spread check.
    spread = get_current_spread(symbol) if mt5_connected else 0.0
    if spread > max_spread > 0:
        logger_.info(
            "Skipping %s — spread %.1f > max %.1f pips.",
            signal_str,
            spread,
            max_spread,
        )
        return

    # SL/TP calculation.
    sl_price, tp_price = calculate_sl_tp(df, signal_str, sl_mult, tp_mult)
    if sl_price <= 0 or tp_price <= 0:
        return

    # Risk:reward check.
    last_close = float(df["close"].iloc[-1])
    if signal_str == "BUY":
        sl_dist = last_close - sl_price
        tp_dist = tp_price - last_close
    else:
        sl_dist = sl_price - last_close
        tp_dist = last_close - tp_price

    if sl_dist > 0 and (tp_dist / sl_dist) < min_rr:
        logger_.info(
            "Skipping %s — risk:reward %.2f < min %.2f.",
            signal_str,
            tp_dist / sl_dist,
            min_rr,
        )
        return

    # Position size in pips.
    sl_pips = _price_distance_to_pips(sl_dist, symbol)
    volume = calculate_position_size(balance, risk_pct, sl_pips, symbol, config)

    if volume <= 0:
        logger_.warning("Calculated volume is zero — skipping entry.")
        return

    # Trade record (for state tracking).
    trade_record = {
        "action": "ENTRY",
        "symbol": symbol,
        "signal": signal_str,
        "volume": volume,
        "entry_price": last_close,
        "sl": sl_price,
        "tp": tp_price,
        "spread": spread,
        "timestamp": time.time(),
    }

    if demo_mode or not mt5_connected:
        logger_.info(
            "[DEMO] Would place %s: %s %.2f lots @ %.5f (SL=%.5f TP=%.5f)",
            signal_str,
            symbol,
            volume,
            last_close,
            sl_price,
            tp_price,
        )
        trade_record["demo"] = True
        state.add_trade(trade_record)
        return

    ticket = place_order(symbol, signal_str, volume, sl_price, tp_price, config)
    if ticket is not None:
        trade_record["ticket"] = ticket
        state.add_trade(trade_record)
    else:
        state.add_error(f"Failed to place {signal_str} order.", "entry")


def _handle_existing(
    config: dict,
    df,
    signal_str: str,
    open_positions: list,
    symbol: str,
    sl_mult: float,
    tp_mult: float,
    risk_pct: float,
    min_rr: float,
    max_spread: float,
    balance: float,
    magic: int,
    mt5_connected: bool,
    demo_mode: bool,
    state: BotState,
) -> None:
    """Handle the case where we have an open position and a new signal arrives."""
    logger_ = logging.getLogger("bot")

    # Determine the direction of existing positions.
    buy_positions = [p for p in open_positions if p["type"] == "BUY"]
    sell_positions = [p for p in open_positions if p["type"] == "SELL"]

    has_buy = len(buy_positions) > 0
    has_sell = len(sell_positions) > 0

    # Check for reversal.
    need_close = False
    if signal_str == "SELL" and has_buy:
        need_close = True
        positions_to_close = buy_positions
    elif signal_str == "BUY" and has_sell:
        need_close = True
        positions_to_close = sell_positions
    else:
        # Same direction — no action (let the existing position ride).
        return

    if not need_close:
        return

    logger_.info("Signal reversed to %s — closing %d position(s).", signal_str, len(positions_to_close))

    # Close opposing positions.
    from executor import close_position as _close_pos
    for pos in positions_to_close:
        if demo_mode or not mt5_connected:
            logger_.info("[DEMO] Would close position #%d.", pos["ticket"])
            trade_record = {
                "action": "CLOSE",
                "symbol": symbol,
                "ticket": pos["ticket"],
                "profit": pos.get("profit", 0),
                "reason": f"signal reversed to {signal_str}",
                "demo": True,
                "timestamp": time.time(),
            }
            state.add_trade(trade_record)
        else:
            success = _close_pos(pos, config=config)
            if success:
                trade_record = {
                    "action": "CLOSE",
                    "symbol": symbol,
                    "ticket": pos["ticket"],
                    "profit": pos.get("profit", 0),
                    "reason": f"signal reversed to {signal_str}",
                    "timestamp": time.time(),
                }
                state.add_trade(trade_record)

    # After closing, try to enter the opposite direction.
    remaining = get_open_positions(symbol=symbol, magic=magic)
    if len(remaining) == 0:
        _try_enter(
            config, df, signal_str, symbol, balance, max_spread,
            sl_mult, tp_mult, risk_pct, min_rr, magic,
            mt5_connected, demo_mode, state,
        )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _update_and_export(
    state: BotState,
    mt5_connected: bool,
    magic: int,
    symbol: str,
    last_export_time: float,
    export_interval: float,
) -> float:
    """Refresh positions/account in state and export if the interval has passed."""
    now = time.time()
    if mt5_connected:
        acct = get_account_info()
        state.update_account(acct)
        positions = get_open_positions(symbol=symbol, magic=magic)
        pnl = sum(p.get("profit", 0.0) for p in positions)
        state.update_positions(positions_to_summary(positions), pnl=pnl)

    if now - last_export_time >= export_interval:
        state.export_to_file()
        return now
    return last_export_time


def _sleep_remaining(cycle_start: float, interval: float) -> None:
    """Sleep for the remainder of *interval* since *cycle_start*."""
    elapsed = time.time() - cycle_start
    remaining = max(0.0, interval - elapsed)
    if remaining > 0 and not _shutdown_requested:
        # Sleep in 1-second chunks so we remain responsive to shutdown.
        while remaining > 0 and not _shutdown_requested:
            time.sleep(min(1.0, remaining))
            remaining -= 1.0


def _price_distance_to_pips(distance: float, symbol: str) -> float:
    """Convert a price distance to an approximate pip value."""
    try:
        from executor import _get_symbol_info
        info = _get_symbol_info(symbol)
        if info:
            point = info.get("point", 0.0001)
            digits = info.get("digits", 5)
            pip_size = point if digits <= 4 else point * 10
            if pip_size > 0:
                return distance / pip_size
    except Exception:
        pass
    # Fallback: assume 4-digit forex.
    return distance / 0.0001


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AlgoTrade MT5 — Moving Average Crossover Bot"
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to YAML config file (default: {DEFAULT_CONFIG})",
    )
    args = parser.parse_args()

    try:
        run_bot(args.config)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
