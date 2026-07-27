"""
state.py — Thread-safe shared state for the trading bot.

Exposes a dictionary-like object that can be safely read and written from
the bot loop and (later) a web dashboard. The state is periodically
serialised to a JSON file so external processes can consume it without
importing any MT5-dependent code.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default path for the JSON export (relative to the bot directory).
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


class BotState:
    """Thread-safe bag of state fields shared across the bot and dashboard."""

    def __init__(self, max_trades: int = 50, max_errors: int = 20) -> None:
        self._lock = threading.Lock()

        # --- Status ---
        self._status: str = "stopped"  # stopped | starting | running | error
        self._start_time: Optional[float] = None

        # --- Signal ---
        self._last_signal: str = "HOLD"
        self._last_check_time: Optional[float] = None

        # --- Account ---
        self._account_info: Dict[str, Any] = {}

        # --- Positions ---
        self._open_positions: List[Dict[str, Any]] = []

        # --- P&L ---
        self._pnl: float = 0.0

        # --- History (bounded) ---
        self._max_trades = max_trades
        self._recent_trades: Deque[Dict[str, Any]] = deque(maxlen=max_trades)

        # --- Errors (bounded) ---
        self._max_errors = max_errors
        self._errors: Deque[Dict[str, Any]] = deque(maxlen=max_errors)

    # ------------------------------------------------------------------
    # Atomic readers
    # ------------------------------------------------------------------

    def get_status(self) -> str:
        with self._lock:
            return self._status

    def get_last_signal(self) -> str:
        with self._lock:
            return self._last_signal

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._open_positions)

    def get_account_info(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._account_info)

    def get_recent_trades(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._recent_trades)

    def get_errors(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._errors)

    # ------------------------------------------------------------------
    # Atomic writers
    # ------------------------------------------------------------------

    def update_status(self, status: str) -> None:
        """Set bot status.  Valid values: 'stopped', 'starting', 'running', 'error'."""
        with self._lock:
            self._status = status
            if status == "starting" and self._start_time is None:
                self._start_time = time.time()

    def update_signal(self, signal: str) -> None:
        with self._lock:
            self._last_signal = signal
            self._last_check_time = time.time()

    def update_account(self, info: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            if info is not None:
                self._account_info = info

    def update_positions(self, positions: List[Dict[str, Any]], pnl: float = 0.0) -> None:
        with self._lock:
            self._open_positions = list(positions) if positions else []
            self._pnl = pnl

    def update_pnl(self, pnl: float) -> None:
        with self._lock:
            self._pnl = pnl

    def add_trade(self, trade: Dict[str, Any]) -> None:
        """Record a completed trade (opened or closed)."""
        trade["recorded_at"] = time.time()
        with self._lock:
            self._recent_trades.append(trade)

    def add_error(self, error: str, context: Optional[str] = None) -> None:
        """Log an error into the state ring buffer."""
        entry: Dict[str, Any] = {
            "timestamp": time.time(),
            "error": error,
        }
        if context:
            entry["context"] = context
        with self._lock:
            self._errors.append(entry)
        logger.error("[%s] %s", context or "bot", error)

    # ------------------------------------------------------------------
    # Snapshot (used by dashboard and file export)
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return a complete, consistent copy of the current state."""
        with self._lock:
            return {
                "status": self._status,
                "start_time": self._start_time,
                "last_signal": self._last_signal,
                "last_check_time": self._last_check_time,
                "account_info": dict(self._account_info),
                "open_positions": list(self._open_positions),
                "pnl": self._pnl,
                "recent_trades": list(self._recent_trades),
                "errors": list(self._errors),
            }

    # ------------------------------------------------------------------
    # File export
    # ------------------------------------------------------------------

    def export_to_file(self, filepath: Optional[str] = None) -> bool:
        """Write the current state snapshot to a JSON file.

        This is designed to be called periodically from the bot loop so that
        the dashboard can read the state without importing any MT5 modules.
        """
        path = filepath or DEFAULT_STATE_FILE
        try:
            snapshot = self.snapshot()
            # Write atomically: temp + rename.
            tmp_path = path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
            os.replace(tmp_path, path)
            return True
        except (OSError, TypeError) as exc:
            logger.error("Failed to export state to '%s': %s", path, exc)
            return False


# ---------------------------------------------------------------------------
# Module-level singleton (optional convenience)
# ---------------------------------------------------------------------------
_global_state: Optional[BotState] = None


def get_global_state() -> BotState:
    """Return (or create) the module-level BotState singleton."""
    global _global_state
    if _global_state is None:
        _global_state = BotState()
    return _global_state
