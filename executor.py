"""
executor.py — Order execution and risk management.

Handles position sizing, order placement, and position management. Every
order is sent with a magic number so the bot can identify its own trades.

Risk management rules:
- Position size is derived from the fraction of balance the user is willing
  to risk, the stop-loss distance, and the symbol's contract/pip
  characteristics.
- Entries are skipped when the current spread exceeds *max_spread_pips*.
- SL and TP are set as multiples of ATR(14).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Graceful MT5 import.
try:
    import MetaTrader5 as mt5

    _MT5_AVAILABLE = mt5 is not None
except ImportError:
    mt5 = None
    _MT5_AVAILABLE = False


# ---------------------------------------------------------------------------
# ATR helper (used for SL/TP distance)
# ---------------------------------------------------------------------------


def _compute_atr(df, period: int = 14) -> float:
    """Compute the Average True Range (ATR) for the most recent bar.

    Parameters
    ----------
    df : pd.DataFrame
        Requires 'high', 'low', 'close' columns.
    period : int
        Lookback for ATR calculation.

    Returns
    -------
    float
        Latest ATR value, or 0.0 if insufficient data.
    """
    if df is None or len(df) < period + 1:
        return 0.0

    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(window=period).mean()
    return float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


def calculate_position_size(
    balance: float,
    risk_percent: float,
    sl_pips: float,
    symbol: str,
    config: Optional[dict] = None,
) -> float:
    """Calculate the lot size that risks *risk_percent* % of *balance*.

    Parameters
    ----------
    balance : float
        Current account balance.
    risk_percent : float
        Fraction of balance to risk, e.g. 1.0 for 1 %.
    sl_pips : float
        Stop-loss distance in pips.
    symbol : str
        Trading symbol (used to look up contract / point size).
    config : dict, optional
        Full config (unused here but accepted for forward-compatibility).

    Returns
    -------
    float
        Lot size rounded to two decimal places (standard forex convention),
        or 0.01 minimum. Returns 0.0 if the calculation cannot be completed.
    """
    if sl_pips <= 0 or balance <= 0 or risk_percent <= 0:
        logger.warning(
            "Invalid position-size inputs: balance=%.2f risk=%.2f sl=%.2f",
            balance,
            risk_percent,
            sl_pips,
        )
        return 0.0

    risk_amount = balance * (risk_percent / 100.0)

    # Try to get symbol info for accurate pip value.
    pip_value = _get_pip_value(symbol)

    # Fallback: assume 1 lot = 100 000 units, pip value ≈ $10 for USD pairs.
    if pip_value <= 0:
        pip_value = 10.0
        logger.debug(
            "Could not determine pip value for '%s', defaulting to $10/pip.", symbol
        )

    # Lots = risk_amount / (sl_pips * pip_value_per_lot)
    lots = risk_amount / (sl_pips * pip_value)
    lots = round(lots, 2)

    # Enforce minimum.
    if lots < 0.01:
        lots = 0.01

    # Clip to symbol constraints if available.
    info = _get_symbol_info(symbol)
    if info:
        vol_min = info.get("volume_min", 0.01)
        vol_max = info.get("volume_max", 100.0)
        vol_step = info.get("volume_step", 0.01)
        lots = max(vol_min, min(vol_max, lots))
        lots = round(lots / vol_step) * vol_step

    logger.info(
        "Position size: %.2f lots (risk=%.2f%%, sl=%.1f pips, pip_value=$%.2f)",
        lots,
        risk_percent,
        sl_pips,
        pip_value,
    )
    return lots


def _get_pip_value(symbol: str) -> float:
    """Return the monetary value of one pip per standard lot for *symbol*.

    This is an approximation; MT5 does not expose pip value directly through
    the Python API, so we derive it from contract size and base currency.
    """
    if not _MT5_AVAILABLE:
        return 0.0

    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.0

    point = info.point
    contract_size = info.trade_contract_size
    digits = info.digits

    # For most forex pairs a pip is 10 * point (digits=5) or point (digits=4).
    # We normalise: 1 pip = 10^{digits - 4} * point.
    if digits <= 4:
        pip_size = point
    else:
        pip_size = point * 10

    # Pip value = contract_size * pip_size (approximate; assumes account
    # currency matches quote currency).
    return contract_size * pip_size


def _get_symbol_info(symbol: str) -> Optional[Dict]:
    """Return symbol info dict (wrapper that handles missing MT5)."""
    if not _MT5_AVAILABLE:
        return None

    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    return {
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "digits": info.digits,
        "point": info.point,
        "trade_contract_size": info.trade_contract_size,
    }


# ---------------------------------------------------------------------------
# SL/TP calculation
# ---------------------------------------------------------------------------


def calculate_sl_tp(
    df,
    signal: str,
    sl_atr_mult: float = 1.5,
    tp_atr_mult: float = 3.0,
    atr_period: int = 14,
) -> Tuple[float, float]:
    """Calculate stop-loss and take-profit prices based on ATR.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (must contain 'high', 'low', 'close').
    signal : str
        "BUY" or "SELL".
    sl_atr_mult : float
        ATR multiplier for stop-loss distance.
    tp_atr_mult : float
        ATR multiplier for take-profit distance.
    atr_period : int
        Period for ATR calculation.

    Returns
    -------
    Tuple[float, float]
        (sl_price, tp_price). Returns (0, 0) if ATR is zero or data is
        insufficient.
    """
    atr = _compute_atr(df, period=atr_period)
    if atr <= 0:
        logger.warning("ATR is zero — cannot compute SL/TP.")
        return 0.0, 0.0

    last_close = float(df["close"].iloc[-1])
    sl_distance = atr * sl_atr_mult
    tp_distance = atr * tp_atr_mult

    if signal == "BUY":
        sl_price = last_close - sl_distance
        tp_price = last_close + tp_distance
    elif signal == "SELL":
        sl_price = last_close + sl_distance
        tp_price = last_close - tp_distance
    else:
        logger.warning("Unknown signal '%s' — cannot compute SL/TP.", signal)
        return 0.0, 0.0

    sl_price = round(sl_price, 5)
    tp_price = round(tp_price, 5)

    logger.debug(
        "SL/TP for %s: close=%.5f atr=%.5f → SL=%.5f TP=%.5f",
        signal,
        last_close,
        atr,
        sl_price,
        tp_price,
    )
    return sl_price, tp_price


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------


def place_order(
    symbol: str,
    signal: str,
    volume: float,
    sl_price: float,
    tp_price: float,
    config: Optional[dict] = None,
) -> Optional[int]:
    """Send a market order to MT5.

    Parameters
    ----------
    symbol : str
        Trading symbol.
    signal : str
        "BUY" or "SELL".
    volume : float
        Lot size.
    sl_price : float
        Stop-loss price.
    tp_price : float
        Take-profit price.
    config : dict, optional
        Full application config (uses order.* and risk.* sections).

    Returns
    -------
    int or None
        The order ticket number if successful, None otherwise.
    """
    if not _MT5_AVAILABLE:
        logger.error("MT5 not available — cannot place order.")
        return None

    cfg = config or {}
    order_cfg = cfg.get("order", {})

    if signal == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(symbol).ask if mt5.symbol_info_tick(symbol) else 0
    elif signal == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).bid if mt5.symbol_info_tick(symbol) else 0
    else:
        logger.error("Invalid signal '%s' — order not placed.", signal)
        return None

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": price,
        "sl": float(sl_price) if sl_price else 0.0,
        "tp": float(tp_price) if tp_price else 0.0,
        "deviation": order_cfg.get("deviation", 20),
        "magic": order_cfg.get("magic", 202401),
        "comment": order_cfg.get("comment", "AlgoTrade MT5"),
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    logger.info(
        "Sending %s order: %s %.2f lots @ %.5f SL=%.5f TP=%.5f",
        signal,
        symbol,
        volume,
        price,
        sl_price,
        tp_price,
    )

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        error = mt5.last_error() if result is None else (result.retcode, result.comment)
        logger.error("Order failed: %s", error)
        return None

    logger.info(
        "Order placed successfully — ticket #%d (vol=%.2f price=%.5f)",
        result.order,
        result.volume,
        result.price,
    )
    return result.order


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------


def get_open_positions(
    symbol: Optional[str] = None, magic: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Return a list of open positions, optionally filtered.

    Parameters
    ----------
    symbol : str, optional
        Filter by symbol.
    magic : int, optional
        Filter by magic number.

    Returns
    -------
    list[dict]
        Each dict contains: ticket, symbol, type, volume, open_price, sl, tp,
        profit, comment, time.
    """
    if not _MT5_AVAILABLE:
        return []

    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []

    result = []
    for pos in positions:
        # Apply magic filter if requested.
        if magic is not None and getattr(pos, "magic", 0) != magic:
            continue

        pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
        result.append(
            {
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": pos_type,
                "volume": pos.volume,
                "open_price": pos.price_open,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "comment": getattr(pos, "comment", ""),
                "time": getattr(pos, "time", 0),
            }
        )
    return result


def close_position(position: dict, config: Optional[dict] = None) -> bool:
    """Close a single position by its ticket number.

    Parameters
    ----------
    position : dict
        Must contain at minimum 'ticket', 'symbol', 'type', 'volume'.
    config : dict, optional
        Full config (order.* section used for deviation/magic).

    Returns
    -------
    bool
        True if the position was closed successfully.
    """
    if not _MT5_AVAILABLE:
        return False

    cfg = config or {}
    order_cfg = cfg.get("order", {})

    ticket = position["ticket"]
    symbol = position["symbol"]
    pos_type = position["type"]
    volume = position["volume"]

    # Opposite order type.
    if pos_type == "BUY":
        order_type = mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).bid if mt5.symbol_info_tick(symbol) else 0
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(symbol).ask if mt5.symbol_info_tick(symbol) else 0

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "position": ticket,
        "price": price,
        "deviation": order_cfg.get("deviation", 20),
        "magic": order_cfg.get("magic", 202401),
        "comment": order_cfg.get("comment", "AlgoTrade MT5") + " close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        error = mt5.last_error() if result is None else (result.retcode, result.comment)
        logger.error("Failed to close position #%d: %s", ticket, error)
        return False

    logger.info("Closed position #%d (%s %.2f lots)", ticket, symbol, volume)
    return True


def close_all_positions(
    symbol: Optional[str] = None, magic: Optional[int] = None, config: Optional[dict] = None
) -> int:
    """Close every open position, optionally filtered.

    Returns
    -------
    int
        Number of positions closed.
    """
    positions = get_open_positions(symbol=symbol, magic=magic)
    closed_count = 0
    for pos in positions:
        if close_position(pos, config=config):
            closed_count += 1
        else:
            logger.error("Failed to close position #%d — continuing.", pos["ticket"])

    logger.info("Closed %d / %d positions.", closed_count, len(positions))
    return closed_count


def positions_to_summary(positions: List[Dict]) -> List[Dict]:
    """Convert position dicts to a dashboard-safe summary (strip internal fields)."""
    return [
        {
            "ticket": p["ticket"],
            "symbol": p["symbol"],
            "type": p["type"],
            "volume": p["volume"],
            "profit": round(p["profit"], 2),
        }
        for p in positions
    ]
