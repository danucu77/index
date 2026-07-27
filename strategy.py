"""
strategy.py — Moving Average Crossover strategy.

Computes two simple moving averages (fast and slow) and generates BUY/SELL
signals on crossovers:

  - BUY  when the fast MA crosses ABOVE the slow MA.
  - SELL when the fast MA crosses BELOW the slow MA.
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Column names used in the DataFrame (avoid magic strings).
COL_FAST_MA = "fast_ma"
COL_SLOW_MA = "slow_ma"
COL_SIGNAL = "signal"
COL_PREV_SIGNAL = "prev_signal"


def compute_signals(
    df: pd.DataFrame,
    fast_period: int = 10,
    slow_period: int = 30,
) -> pd.DataFrame:
    """Add moving average and signal columns to *df*.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain at minimum a 'close' column.
    fast_period : int
        Lookback for the fast simple moving average.
    slow_period : int
        Lookback for the slow simple moving average.

    Returns
    -------
    pd.DataFrame
        The input DataFrame with additional columns:
        - 'fast_ma'
        - 'slow_ma'
        - 'signal'    : 1 = BUY, -1 = SELL, 0 = HOLD (only on crossover bars)
        - 'prev_signal' : shifted signal column (used for crossover detection)
    """
    if df is None or len(df) < slow_period:
        logger.warning(
            "Insufficient data for signal computation (need %d, got %d).",
            slow_period,
            len(df) if df is not None else 0,
        )
        return df

    df = df.copy()

    # Simple moving averages.
    df[COL_FAST_MA] = df["close"].rolling(window=fast_period).mean()
    df[COL_SLOW_MA] = df["close"].rolling(window=slow_period).mean()

    # Position relative to the slow MA for the *current* bar:
    # fast > slow  →  1 (bullish)
    # fast < slow  → -1 (bearish)
    # fast == slow →  0
    df[COL_SIGNAL] = 0
    mask_bull = df[COL_FAST_MA] > df[COL_SLOW_MA]
    mask_bear = df[COL_FAST_MA] < df[COL_SLOW_MA]
    df.loc[mask_bull, COL_SIGNAL] = 1
    df.loc[mask_bear, COL_SIGNAL] = -1

    # Previous bar's signal for crossover detection.
    df[COL_PREV_SIGNAL] = df[COL_SIGNAL].shift(1).fillna(0).astype(int)

    # Detect crossovers — only flag the bar where the crossover happens.
    # BUY crossover:  prev <= 0 and now == 1
    # SELL crossover: prev >= 0 and now == -1
    crossover_buy = (df[COL_PREV_SIGNAL] <= 0) & (df[COL_SIGNAL] == 1)
    crossover_sell = (df[COL_PREV_SIGNAL] >= 0) & (df[COL_SIGNAL] == -1)

    # Zero out the signal column for non-crossover bars (HOLD).
    df[COL_SIGNAL] = 0
    df.loc[crossover_buy, COL_SIGNAL] = 1
    df.loc[crossover_sell, COL_SIGNAL] = -1

    return df


def get_latest_signal(df: pd.DataFrame) -> str:
    """Return the latest trading signal from a DataFrame that has been
    processed by :func:`compute_signals`.

    Returns
    -------
    str
        One of ``"BUY"``, ``"SELL"``, or ``"HOLD"``.
    """
    if df is None or len(df) == 0 or COL_SIGNAL not in df.columns:
        return "HOLD"

    last = df[COL_SIGNAL].iloc[-1]
    if last == 1:
        return "BUY"
    elif last == -1:
        return "SELL"
    return "HOLD"


def get_signal_strength(df: pd.DataFrame) -> float:
    """Return the latest signal as a numeric value.

    Returns
    -------
    float
         1.0 = BUY, -1.0 = SELL, 0.0 = HOLD.
    """
    if df is None or len(df) == 0 or COL_SIGNAL not in df.columns:
        return 0.0

    return float(df[COL_SIGNAL].iloc[-1])
