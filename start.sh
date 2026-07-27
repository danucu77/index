#!/usr/bin/env bash
# =============================================================================
# AlgoTrade MT5 — Master Start Script
# =============================================================================
# Starts both the web dashboard (background) and the trading bot (foreground).
# Press Ctrl+C to stop both gracefully.
#
# Usage:
#   ./start.sh              # Uses demo_mode from config.yaml
#   ./start.sh --dry-run    # Force demo_mode: true
#   ./start.sh --live       # Force demo_mode: false (REAL MONEY!)
#
# Environment variables (optional):
#   BOT_STATE_FILE    — path to state.json (default: bot/state.json)
#   DASHBOARD_PORT    — dashboard port (default: 3000)
#   DASHBOARD_HOST    — dashboard bind address (default: 0.0.0.0)
#   STALE_SECONDS     — stale threshold for dashboard (default: 60)
# =============================================================================
set -euo pipefail

# --- Resolve paths -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DASHBOARD_DIR="${SCRIPT_DIR}/dashboard"
DASHBOARD_PID=""
DASHBOARD_LOG="/tmp/algotrade_dashboard.log"

# --- Parse flags -------------------------------------------------------------
DRY_RUN=""
LIVE=""
MODE_FLAG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN="1"
            MODE_FLAG="--dry-run"
            shift
            ;;
        --live)
            LIVE="1"
            MODE_FLAG="--live"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--dry-run|--live]"
            echo ""
            echo "  --dry-run   Force demo mode (no real orders)"
            echo "  --live      Force live mode (REAL orders — use with caution!)"
            echo ""
            echo "  If neither flag is given, the bot uses demo_mode from config.yaml."
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--dry-run|--live]"
            exit 1
            ;;
    esac
done

if [[ -n "$DRY_RUN" ]] && [[ -n "$LIVE" ]]; then
    echo "ERROR: Cannot use both --dry-run and --live."
    exit 1
fi

# --- Banner ------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              AlgoTrade MT5 — Trading Bot                    ║"
echo "╠══════════════════════════════════════════════════════════════╣"
if [[ -n "$DRY_RUN" ]]; then
    echo "║  Mode: DRY-RUN (no real orders will be placed)              ║"
elif [[ -n "$LIVE" ]]; then
    echo "║  Mode: LIVE ⚡ (REAL orders — use with caution!)            ║"
else
    echo "║  Mode: from config.yaml (demo_mode setting)                 ║"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# --- Install Python dependencies ---------------------------------------------
echo "[1/4] Installing Python dependencies …"
pip install --break-system-packages --quiet -r requirements.txt 2>/dev/null || true

# Check critical deps.
MISSING=""
for pkg in yaml pandas numpy; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
        MISSING="$MISSING $pkg"
    fi
done
if [[ -n "$MISSING" ]]; then
    echo "   Installing missing packages:$MISSING …"
    pip install --break-system-packages $MISSING
fi

# Flask is needed for the dashboard.
if ! python3 -c "import flask" 2>/dev/null; then
    echo "   Installing Flask for dashboard …"
    pip install --break-system-packages flask
fi
echo "   ✓ Dependencies OK"

# --- Free port 3000 -----------------------------------------------------------
PORT="${DASHBOARD_PORT:-3000}"
echo "[2/4] Freeing port ${PORT} …"
sudo sh -c "lsof -t -iTCP:${PORT} -sTCP:LISTEN | xargs -r kill" 2>/dev/null || true
sleep 0.5
echo "   ✓ Port ${PORT} freed"

# --- Start dashboard in background --------------------------------------------
echo "[3/4] Starting dashboard on ${DASHBOARD_HOST:-0.0.0.0}:${PORT} …"
cd "$DASHBOARD_DIR"
nohup python3 app.py > "$DASHBOARD_LOG" 2>&1 &
DASHBOARD_PID=$!
cd "$SCRIPT_DIR"
echo "   Dashboard PID: $DASHBOARD_PID (log: $DASHBOARD_LOG)"

# Give it a moment to start.
sleep 1
if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
    echo "   ❌ Dashboard failed to start! Check $DASHBOARD_LOG"
    exit 1
fi
echo "   ✓ Dashboard running"

# --- Cleanup handler ----------------------------------------------------------
cleanup() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  Shutting down AlgoTrade MT5 …                             ║"
    echo "╚══════════════════════════════════════════════════════════════╝"

    # Stop dashboard.
    if [[ -n "$DASHBOARD_PID" ]] && kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        echo "   Stopping dashboard (PID: $DASHBOARD_PID) …"
        kill "$DASHBOARD_PID" 2>/dev/null || true
        wait "$DASHBOARD_PID" 2>/dev/null || true
        echo "   ✓ Dashboard stopped"
    fi

    # Clean up leftover port 3000 processes (belt and suspenders).
    sudo sh -c "lsof -t -iTCP:${PORT} -sTCP:LISTEN | xargs -r kill" 2>/dev/null || true

    echo "   Goodbye!"
    exit 0
}

trap cleanup SIGINT SIGTERM

# --- Start bot in foreground --------------------------------------------------
echo "[4/4] Starting trading bot …"
echo ""
cd "$SCRIPT_DIR"
python3 bot.py $MODE_FLAG

# If bot exits on its own, still clean up.
cleanup
