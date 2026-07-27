#!/usr/bin/env python3
"""
AlgoTrade MT5 — Web Monitoring Dashboard
Serves on 0.0.0.0:3000, reads state from /home/team/shared/bot/state.json
"""

import json
import os
import time
from datetime import datetime, timedelta
from flask import Flask, render_template_string

# ── configurable ───────────────────────────────────────────────────────
STATE_FILE = os.environ.get("BOT_STATE_FILE", "/home/team/shared/bot/state.json")
STALE_SECONDS = int(os.environ.get("STALE_SECONDS", 60))
HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.environ.get("DASHBOARD_PORT", 3000))
# ───────────────────────────────────────────────────────────────────────

app = Flask(__name__)

# ── state helpers ─────────────────────────────────────────────────────
def load_state():
    """Load bot state from JSON file. Returns (data, stale, error)."""
    if not os.path.exists(STATE_FILE):
        return None, False, "State file not found — waiting for bot to start."
    try:
        with open(STATE_FILE, "r") as f:
            raw = f.read().strip()
        if not raw:
            return None, False, "State file is empty — waiting for bot data."
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        return None, False, f"State file is corrupt: {e}"
    except Exception as e:
        return None, False, f"Error reading state file: {e}"

    mtime = os.path.getmtime(STATE_FILE)
    stale = (time.time() - mtime) > STALE_SECONDS
    return data, stale, None


def fmt_pnl(val):
    """Format a P&L value with color class and currency sign."""
    if val is None:
        return "$0.00", ""
    sign = "+" if val > 0 else ""
    cls = "positive" if val > 0 else ("negative" if val < 0 else "")
    return f"{sign}${val:,.2f}", cls


def fmt_time(ts):
    """Format a unix timestamp to readable string."""
    if ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(ts)


def calc_uptime(start_ts):
    """Calculate uptime string from start timestamp."""
    if start_ts is None:
        return "—"
    try:
        delta = timedelta(seconds=time.time() - start_ts)
        days = delta.days
        hrs, rem = divmod(delta.seconds, 3600)
        mins, secs = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hrs or parts:
            parts.append(f"{hrs}h")
        if mins or parts:
            parts.append(f"{mins}m")
        parts.append(f"{secs}s")
        return " ".join(parts)
    except (TypeError, ValueError):
        return "—"


def win_rate(trades):
    """Calculate win rate from a list of trades with 'profit' keys."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("profit", 0) > 0)
    return round(wins / len(trades) * 100, 1)


def profit_factor(trades):
    """Calculate profit factor (gross_profit / |gross_loss|)."""
    gross_profit = sum(t.get("profit", 0) for t in trades if t.get("profit", 0) > 0)
    gross_loss = abs(sum(t.get("profit", 0) for t in trades if t.get("profit", 0) < 0))
    if gross_loss == 0:
        return gross_profit if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 2)
# ───────────────────────────────────────────────────────────────────────


# ── routes ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    data, stale, error = load_state()

    return render_template_string(TEMPLATE, **build_context(data, stale, error))


@app.route("/health")
def health():
    return {"status": "ok", "timestamp": time.time()}
# ───────────────────────────────────────────────────────────────────────


def build_context(data, stale, error):
    """Build the Jinja2 template context from state data."""
    ctx = {
        "error": error,
        "stale": stale,
        "now": fmt_time(time.time()),
        "status": "unknown",
        "status_class": "status-unknown",
        "uptime": "—",
        "last_signal": "—",
        "last_check": "—",
        "account": {},
        "has_account": False,
        "positions": [],
        "trades": [],
        "errors_list": [],
        "pnl_total": "$0.00",
        "pnl_total_class": "",
        "win_rate_val": "—",
        "total_trades": 0,
        "profit_factor_val": "—",
    }

    if data:
        status = data.get("status", "unknown")
        ctx["status"] = status.upper()
        ctx["status_class"] = {
            "running": "status-running",
            "stopped": "status-stopped",
            "error": "status-error",
        }.get(status, "status-unknown")

        ctx["uptime"] = calc_uptime(data.get("start_time"))
        ctx["last_signal"] = data.get("last_signal", "—")
        ctx["last_check"] = fmt_time(data.get("last_check_time"))

        acct = data.get("account_info", {}) or {}
        if acct.get("balance"):
            ctx["account"] = acct
            ctx["has_account"] = True

        # Open positions
        ctx["positions"] = data.get("open_positions", [])

        # Recent trades (last 50)
        ctx["trades"] = data.get("recent_trades", [])[-50:][::-1]

        # P&L
        pnl_total = data.get("pnl", 0.0)
        ctx["pnl_total"], ctx["pnl_total_class"] = fmt_pnl(pnl_total)

        all_trades = data.get("recent_trades", [])
        ctx["total_trades"] = len(all_trades)
        ctx["win_rate_val"] = f"{win_rate(all_trades):.1f}%"
        pf = profit_factor(all_trades)
        ctx["profit_factor_val"] = f"{pf:.2f}"

        # Errors (last 20)
        ctx["errors_list"] = data.get("errors", [])[-20:][::-1]

    return ctx


# ── inline template ───────────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>AlgoTrade MT5 — Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 14px; }
body {
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: #0d1117; color: #c9d1d9; min-height: 100vh;
  line-height: 1.5;
}
.container { max-width: 1200px; margin: 0 auto; padding: 16px 20px; }

/* header */
.header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 0; border-bottom: 1px solid #21262d; margin-bottom: 16px;
  flex-wrap: wrap; gap: 10px;
}
.header h1 { font-size: 1.4rem; color: #58a6ff; font-weight: 600; }
.header .timestamp { font-size: 0.85rem; color: #8b949e; }

/* banners */
.banner {
  padding: 10px 16px; border-radius: 6px; margin-bottom: 16px;
  font-size: 0.9rem; font-weight: 500;
}
.banner-warn { background: #3d2e00; color: #f0c040; border: 1px solid #5a4300; }
.banner-error { background: #3d1a1a; color: #f06060; border: 1px solid #5a1a1a; }
.banner-info { background: #0d3030; color: #56d4dd; border: 1px solid #1a4a4a; }

/* status indicator */
.status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.status-running { background: #3fb950; box-shadow: 0 0 6px #3fb950; }
.status-stopped { background: #8b949e; }
.status-error { background: #f85149; box-shadow: 0 0 6px #f85149; }
.status-unknown { background: #d29922; }

/* cards grid */
.card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; }
.card-header {
  font-size: 0.85rem; font-weight: 600; color: #8b949e; text-transform: uppercase;
  letter-spacing: 0.05em; margin-bottom: 10px; border-bottom: 1px solid #21262d;
  padding-bottom: 8px;
}
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 16px; }

/* account stats */
.stat-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.9rem; }
.stat-label { color: #8b949e; }
.stat-value { font-weight: 600; color: #e6edf3; }
.positive { color: #3fb950 !important; }
.negative { color: #f85149 !important; }

/* tables */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #21262d; }
th { color: #8b949e; font-weight: 600; white-space: nowrap; }
td { color: #c9d1d9; }
tr:hover td { background: #1c2128; }
.badge-type {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 0.78rem; font-weight: 600; text-transform: uppercase;
}
.badge-buy { background: #1a3d1a; color: #3fb950; }
.badge-sell { background: #3d1a1a; color: #f85149; }

/* pnl summary cards */
.summary-card { text-align: center; }
.summary-value { font-size: 2rem; font-weight: 700; }
.summary-label { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }

/* errors */
details summary { cursor: pointer; color: #8b949e; font-weight: 600; }
details[open] summary { color: #f0c040; }
.error-item {
  padding: 6px 0; border-bottom: 1px solid #21262d; font-size: 0.82rem;
}
.error-time { color: #8b949e; font-size: 0.78rem; }
.error-msg { color: #f06060; }

/* empty state */
.empty { text-align: center; padding: 30px; color: #484f58; font-style: italic; }

/* responsive */
@media (max-width: 768px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
  .summary-value { font-size: 1.5rem; }
}
</style>
</head>
<body>
<div class="container">

  <!-- header -->
  <div class="header">
    <div>
      <h1>⚡ AlgoTrade MT5</h1>
      <span style="font-size:0.85rem;color:#8b949e">Monitoring Dashboard</span>
    </div>
    <div class="timestamp">Last refresh: {{ now }}</div>
  </div>

  <!-- banners -->
  {% if error %}
  <div class="banner banner-error">{{ error }}</div>
  {% endif %}
  {% if stale and not error %}
  <div class="banner banner-warn">⚠️ State data may be stale — state.json hasn't been updated recently.</div>
  {% endif %}
  {% if status == "STOPPED" %}
  <div class="banner banner-info">ℹ️ Bot is currently stopped. Start the bot to see live data.</div>
  {% endif %}

  <!-- status + account grid -->
  <div class="grid-2">
    <!-- status -->
    <div class="card">
      <div class="card-header">🤖 Bot Status</div>
      <div class="stat-row">
        <span class="stat-label">Status</span>
        <span><span class="status-dot {{ status_class }}"></span>{{ status }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Uptime</span>
        <span class="stat-value">{{ uptime }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Last Signal</span>
        <span class="stat-value">{{ last_signal }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Last Check</span>
        <span class="stat-value">{{ last_check }}</span>
      </div>
    </div>

    <!-- account -->
    <div class="card">
      <div class="card-header">💰 Account</div>
      {% if has_account %}
      <div class="stat-row">
        <span class="stat-label">Balance</span>
        <span class="stat-value">${{ "%.2f"|format(account.balance) }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Equity</span>
        <span class="stat-value">${{ "%.2f"|format(account.equity) }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Margin</span>
        <span class="stat-value">${{ "%.2f"|format(account.margin) }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Free Margin</span>
        <span class="stat-value">${{ "%.2f"|format(account.free_margin) }}</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Currency</span>
        <span class="stat-value">{{ account.get("currency", "—") }}</span>
      </div>
      {% else %}
      <div class="empty">No account data — connect MT5 to see account info.</div>
      {% endif %}
    </div>
  </div>

  <!-- P&L summary -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-header">📊 P&L Summary</div>
    <div class="grid-3">
      <div class="summary-card">
        <div class="summary-value {{ pnl_total_class }}">{{ pnl_total }}</div>
        <div class="summary-label">Total P&L</div>
      </div>
      <div class="summary-card">
        <div class="summary-value" style="color:#c9d1d9">{% if total_trades > 0 %}{{ total_trades }}{% else %}—{% endif %}</div>
        <div class="summary-label">Total Trades</div>
      </div>
      <div class="summary-card">
        <div class="summary-value" style="color:#79c0ff">{{ win_rate_val }}</div>
        <div class="summary-label">Win Rate</div>
      </div>
      <div class="summary-card">
        <div class="summary-value" style="color:#c9d1d9">{{ profit_factor_val }}</div>
        <div class="summary-label">Profit Factor</div>
      </div>
    </div>
  </div>

  <!-- open positions -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-header">📈 Open Positions</div>
    {% if positions %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Ticket</th><th>Symbol</th><th>Type</th><th>Volume</th>
            <th>Open Price</th><th>SL</th><th>TP</th><th>Current</th>
            <th>Profit</th><th>Pips</th>
          </tr>
        </thead>
        <tbody>
        {% for p in positions %}
        <tr>
          <td>{{ p.ticket }}</td>
          <td>{{ p.symbol }}</td>
          <td><span class="badge-type {{ 'badge-buy' if p.type == 'BUY' else 'badge-sell' }}">{{ p.type }}</span></td>
          <td>{{ p.volume }}</td>
          <td>{{ "%.5f"|format(p.open_price) }}</td>
          <td>{{ "%.5f"|format(p.sl) if p.sl else "—" }}</td>
          <td>{{ "%.5f"|format(p.tp) if p.tp else "—" }}</td>
          <td>{{ "%.5f"|format(p.current_price) if p.current_price else "—" }}</td>
          <td class="{{ 'positive' if (p.profit or 0) > 0 else ('negative' if (p.profit or 0) < 0 else '') }}">${{ "%.2f"|format(p.profit or 0) }}</td>
          <td class="{{ 'positive' if (p.profit_pips or 0) > 0 else ('negative' if (p.profit_pips or 0) < 0 else '') }}">{{ "%.1f"|format(p.profit_pips or 0) }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% else %}
    <div class="empty">No open positions.</div>
    {% endif %}
  </div>

  <!-- trade history -->
  <div class="card" style="margin-bottom:16px;">
    <div class="card-header">📋 Trade History (last 50)</div>
    {% if trades %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Symbol</th><th>Type</th><th>Volume</th>
            <th>Entry</th><th>Exit</th><th>Profit</th>
          </tr>
        </thead>
        <tbody>
        {% for t in trades %}
        <tr>
          <td>{{ t.timestamp if t.timestamp else "—" }}</td>
          <td>{{ t.symbol }}</td>
          <td><span class="badge-type {{ 'badge-buy' if t.type == 'BUY' else 'badge-sell' }}">{{ t.type }}</span></td>
          <td>{{ t.volume }}</td>
          <td>{{ "%.5f"|format(t.entry_price) if t.entry_price else "—" }}</td>
          <td>{{ "%.5f"|format(t.exit_price) if t.exit_price else "—" }}</td>
          <td class="{{ 'positive' if (t.profit or 0) > 0 else ('negative' if (t.profit or 0) < 0 else '') }}">${{ "%.2f"|format(t.profit or 0) }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% else %}
    <div class="empty">No trade history yet.</div>
    {% endif %}
  </div>

  <!-- errors / warnings -->
  <div class="card" style="margin-bottom:16px;">
    <details {% if errors_list %}open{% endif %}>
      <summary class="card-header" style="display:inline-block;margin-bottom:0;border-bottom:0;padding-bottom:0;">
        ⚠️ Errors & Warnings {% if errors_list %}({{ errors_list|length }}){% endif %}
      </summary>
      <div style="margin-top:10px;">
        {% if errors_list %}
        {% for e in errors_list %}
        <div class="error-item">
          <div class="error-time">{{ fmt_time(e.timestamp) if e.timestamp else "—" }}</div>
          <div class="error-msg">{{ e.error }}</div>
        </div>
        {% endfor %}
        {% else %}
        <div class="empty">No errors or warnings.</div>
        {% endif %}
      </div>
    </details>
  </div>

</div>
</body>
</html>"""

# ── main ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 AlgoTrade MT5 Dashboard starting on {HOST}:{PORT}")
    print(f"   Reading state from: {STATE_FILE}")
    # Monkey-patch the template to have fmt_time available in Jinja2
    app.jinja_env.globals["fmt_time"] = fmt_time
    app.run(host=HOST, port=PORT, debug=False)
