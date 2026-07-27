"""
mt5_connector.py — MetaTrader 5 connection and data utilities.

Handles initialisation, login, rate fetching, spread queries, and account
information. All MT5 API calls are wrapped with error handling and fallbacks
so the bot degrades gracefully when MT5 is not installed.
"""

import logging
import time
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful import — MetaTrader5 is only available when the MT5 terminal
# (or a compatible Wine environment) is installed.
# ---------------------------------------------------------------------------
try:
    import MetaTrader5 as mt5

    _MT5_AVAILABLE = mt5 is not None
except ImportError:
    mt5 = None
    _MT5_AVAILABLE = False

# ---------------------------------------------------------------------------
# Timeframe mapping (string → MT5 constant)
# ---------------------------------------------------------------------------
TIMEFRAME_MAP = {
    "M1": getattr(mt5, "TIMEFRAME_M1", 1) if mt5 else 1,
    "M2": getattr(mt5, "TIMEFRAME_M2", 2) if mt5 else 2,
    "M3": getattr(mt5, "TIMEFRAME_M3", 3) if mt5 else 3,
    "M4": getattr(mt5, "TIMEFRAME_M4", 4) if mt5 else 4,
    "M5": getattr(mt5, "TIMEFRAME_M5", 5) if mt5 else 5,
    "M6": getattr(mt5, "TIMEFRAME_M6", 6) if mt5 else 6,
    "M10": getattr(mt5, "TIMEFRAME_M10", 10) if mt5 else 10,
    "M12": getattr(mt5, "TIMEFRAME_M12", 12) if mt5 else 12,
    "M15": getattr(mt5, "TIMEFRAME_M15", 15) if mt5 else 15,
    "M20": getattr(mt5, "TIMEFRAME_M20", 20) if mt5 else 20,
    "M30": getattr(mt5, "TIMEFRAME_M30", 30) if mt5 else 30,
    "H1": getattr(mt5, "TIMEFRAME_H1", 16385) if mt5 else 16385,
    "H2": getattr(mt5, "TIMEFRAME_H2", 16386) if mt5 else 16386,
    "H3": getattr(mt5, "TIMEFRAME_H3", 16387) if mt5 else 16387,
    "H4": getattr(mt5, "TIMEFRAME_H4", 16388) if mt5 else 16388,
    "H6": getattr(mt5, "TIMEFRAME_H6", 16390) if mt5 else 16390,
    "H8": getattr(mt5, "TIMEFRAME_H8", 16392) if mt5 else 16392,
    "H12": getattr(mt5, "TIMEFRAME_H12", 16396) if mt5 else 16396,
    "D1": getattr(mt5, "TIMEFRAME_D1", 16408) if mt5 else 16408,
    "W1": getattr(mt5, "TIMEFRAME_W1", 32769) if mt5 else 32769,
    "MN1": getattr(mt5, "TIMEFRAME_MN1", 49153) if mt5 else 49153,
}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_mt5_available() -> bool:
    """Return True if the MetaTrader5 package was imported successfully."""
    return _MT5_AVAILABLE


def _resolve_timeframe(tf: str) -> int:
    """Convert a timeframe string (e.g. 'M5') to an MT5 constant."""
    tf_upper = tf.upper()
    if tf_upper not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unknown timeframe '{tf}'. Allowed: {list(TIMEFRAME_MAP.keys())}"
        )
    return TIMEFRAME_MAP[tf_upper]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def initialize_mt5(config: dict) -> bool:
    """Initialise the MT5 terminal and log in.

    Parameters
    ----------
    config : dict
        The full application config (as loaded from config.yaml).

    Returns
    -------
    bool
        True if initialisation and login succeeded.
    """
    if not _MT5_AVAILABLE:
        logger.error(
            "MetaTrader5 package is not available. "
            "Are you running on Windows or Wine with MT5 installed?"
        )
        return False

    mt5_cfg = config.get("mt5", {})

    # Optionally specify the terminal path.
    path = mt5_cfg.get("path", "")
    init_kwargs = {}
    if path:
        init_kwargs["path"] = path

    if not mt5.initialize(**init_kwargs):
        error = mt5.last_error()
        logger.error("MT5 initialisation failed: %s", error)
        return False

    logger.info("MT5 initialised successfully (version %s)", mt5.version())

    # Login (demo or live account).
    login = mt5_cfg.get("login", 0)
    password = mt5_cfg.get("password", "")
    server = mt5_cfg.get("server", "")

    if login and password:
        authorized = mt5.login(
            login=int(login),
            password=str(password),
            server=str(server) if server else "",
        )
        if not authorized:
            error = mt5.last_error()
            logger.error("MT5 login failed: %s", error)
            mt5.shutdown()
            return False
        logger.info("MT5 login successful — account %s on %s", login, server or "default")
    else:
        logger.warning(
            "No MT5 credentials provided; connecting to existing logged-in session."
        )

    return True


def shutdown_mt5() -> None:
    """Shut down the MT5 connection cleanly."""
    if _MT5_AVAILABLE and mt5:
        mt5.shutdown()
        logger.info("MT5 connection shut down.")
    else:
        logger.info("MT5 was not running; nothing to shut down.")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def get_rates(
    symbol: str, timeframe: str, count: int = 100
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV rate data for *symbol*.

    Parameters
    ----------
    symbol : str
        Trading symbol, e.g. "EURUSD".
    timeframe : str
        Timeframe string, e.g. "M5".
    count : int
        Number of bars to fetch.

    Returns
    -------
    pd.DataFrame or None
        DataFrame with columns ['time', 'open', 'high', 'low', 'close',
        'tick_volume', 'spread', 'real_volume'] indexed by time, or None on
        failure.
    """
    if not _MT5_AVAILABLE:
        logger.warning("MT5 not available — returning empty rates.")
        return None

    tf = _resolve_timeframe(timeframe)

    # Ensure the symbol is selected in Market Watch.
    if not mt5.symbol_select(symbol, True):
        logger.error("Failed to select symbol '%s' in Market Watch.", symbol)
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        error = mt5.last_error()
        logger.error("Failed to fetch rates for %s: %s", symbol, error)
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def get_current_spread(symbol: str) -> float:
    """Return the current spread for *symbol* in pips.

    Returns
    -------
    float
        Spread in pips, or -1.0 on failure.
    """
    if not _MT5_AVAILABLE:
        return -1.0

    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error("Failed to get symbol info for '%s'.", symbol)
        return -1.0

    # Compute spread in pips: (ask - bid) / point.
    point = info.point
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error("Failed to get tick for '%s'.", symbol)
        return -1.0

    spread_pips = (tick.ask - tick.bid) / point
    return round(spread_pips, 1)


def get_symbol_info(symbol: str) -> Optional[dict]:
    """Return detailed symbol info as a dict.

    Returns None if the symbol is not available.
    """
    if not _MT5_AVAILABLE:
        return None

    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    return {
        "symbol": info.name,
        "bid": info.bid,
        "ask": info.ask,
        "spread": info.spread,
        "digits": info.digits,
        "point": info.point,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "trade_contract_size": info.trade_contract_size,
    }


def get_account_info() -> Optional[Dict]:
    """Return account information as a dict.

    Returns
    -------
    dict or None
        Keys: login, server, balance, equity, margin, margin_free, margin_level,
        currency, leverage, demo.
    """
    if not _MT5_AVAILABLE:
        return None

    info = mt5.account_info()
    if info is None:
        error = mt5.last_error()
        logger.error("Failed to get account info: %s", error)
        return None

    return {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level,
        "currency": info.currency,
        "leverage": info.leverage,
        "demo": "demo" in (info.server or "").lower()
        or "demo" in str(getattr(info, "company", "")).lower(),
    }


def is_market_open(symbol: str) -> bool:
    """Check whether the market for *symbol* is currently open for trading.

    Returns True if MT5 is unavailable (assume open to avoid blocking).
    """
    if not _MT5_AVAILABLE:
        return True

    info = mt5.symbol_info(symbol)
    if info is None:
        return False

    # trade_mode == 0 means disabled; anything else is tradable.
    trade_mode = getattr(info, "trade_mode", 4)
    return trade_mode != 0
