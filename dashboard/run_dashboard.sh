#!/usr/bin/env bash
# ── AlgoTrade MT5 Dashboard Launcher ───────────────────────────────────
# Usage: ./run_dashboard.sh
# Installs deps (if needed) and starts the web dashboard on port 3000.
#
# Config via environment variables:
#   BOT_STATE_FILE    — path to state.json (default: /home/team/shared/bot/state.json)
#   DASHBOARD_PORT    — port to listen on (default: 3000)
#   DASHBOARD_HOST    — bind address (default: 0.0.0.0)
#   STALE_SECONDS     — seconds before data is considered stale (default: 60)
# ───────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== AlgoTrade MT5 Dashboard ==="

# 1. Install deps ------------------------------------------------
echo "[1/3] Checking dependencies …"
pip3 install --break-system-packages --quiet --ignore-installed flask 2>/dev/null || true

if ! python3 -c "import flask" 2>/dev/null; then
    echo "   Flask not found — installing …"
    pip3 install --break-system-packages flask
fi
echo "   ✓ Flask available"

# 2. Kill any existing process on port 3000 -----------------------
PORT="${DASHBOARD_PORT:-3000}"
echo "[2/3] Freeing port ${PORT} …"
sudo sh -c "lsof -t -iTCP:${PORT} -sTCP:LISTEN | xargs -r kill" 2>/dev/null || true
sleep 0.5
echo "   ✓ Port ${PORT} freed"

# 3. Start dashboard ----------------------------------------------
echo "[3/3] Starting dashboard on ${DASHBOARD_HOST:-0.0.0.0}:${PORT} …"
echo "   State file: ${BOT_STATE_FILE:-/home/team/shared/bot/state.json}"
echo ""
exec python3 app.py
