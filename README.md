# AlgoTrade MT5

**Automated trading bot for MetaTrader 5 with a web monitoring dashboard.**

A Python bot that connects to MetaTrader 5, runs a Moving Average Crossover strategy with configurable risk management, and serves a real-time web dashboard for monitoring positions, P&L, and trade history.

---

## Features

- **MA Crossover Strategy** — BUY/SELL signals generated from fast and slow simple moving average crossovers
- **Risk Management** — Automatic position sizing based on account balance risk percentage, ATR-based stop-loss and take-profit, spread filtering, and minimum risk:reward ratio enforcement
- **Web Dashboard** — Real-time monitoring on port 3000 showing bot status, account info, open positions, P&L summary, trade history, and error log
- **Dry-Run Mode** — Signals are logged but no real orders are sent (safe for testing)
- **Live Mode** — Sends real market orders to your MT5 account
- **Graceful Degradation** — Runs without MT5 installed — the bot logs signals and the dashboard still works
- **Thread-Safe State** — Shared state exported to JSON for the dashboard to consume without importing MT5

---

## Prerequisites

- **MetaTrader 5 terminal** installed on Windows or via Wine on Linux
- **Python 3.10+** (the bot itself runs anywhere; MT5 integration requires Windows/Wine)
- **pip** for Python package management

> **Note:** The `MetaTrader5` Python package is only available when the MT5 terminal is installed. The bot gracefully degrades when MT5 is absent — it logs signals without sending orders and the dashboard remains fully functional.

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/danucu77/index.git
cd index

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Edit the configuration
cp config.yaml config.yaml  # edit in-place with your settings
#   - Set your MT5 login, password, server
#   - Choose your symbol (e.g. EURUSD) and timeframe
#   - Set demo_mode: true for testing

# 4. Start the bot (dashboard + bot together)
./start.sh --dry-run
```

Then open **http://localhost:3000** to view the dashboard.

---

## Configuration Guide

All settings live in `config.yaml`. Key sections:

### MT5 Connection (`mt5:`)
| Setting | Description |
|---------|-------------|
| `path` | Path to MT5 terminal executable (Windows: `"C:/Program Files/MetaTrader 5/terminal64.exe"`). Leave empty on Linux/Wine to connect to a running instance. |
| `login` | Your MT5 account number |
| `password` | Your MT5 account password |
| `server` | MT5 broker server name |

### Trading Symbol (`symbol`)
The instrument to trade. Examples: `EURUSD`, `GBPUSD`, `XAUUSD`, `BTCUSD`.

### Timeframe (`timeframe`)
Bar interval for signal computation. Supported: `M1`, `M2`, `M3`, `M4`, `M5`, `M6`, `M10`, `M12`, `M15`, `M20`, `M30`, `H1`, `H2`, `H3`, `H4`, `H6`, `H8`, `H12`, `D1`, `W1`, `MN1`.

### Strategy (`strategy:`)
| Setting | Default | Description |
|---------|---------|-------------|
| `fast_ma_period` | 10 | Fast moving average period (shorter = more sensitive) |
| `slow_ma_period` | 30 | Slow moving average period (longer = trend baseline) |
| `min_bars` | 50 | Minimum bars to fetch for signal computation |

### Risk Management (`risk:`)
| Setting | Default | Description |
|---------|---------|-------------|
| `risk_percent` | 1.0 | % of account balance to risk per trade |
| `sl_atr_multiplier` | 1.5 | Stop-loss = ATR × this multiplier |
| `tp_atr_multiplier` | 3.0 | Take-profit = ATR × this multiplier |
| `max_spread_pips` | 3.0 | Skip trade if spread exceeds this |
| `min_risk_reward` | 1.5 | Minimum TP:SL distance ratio |

### Bot Behaviour (`bot:`)
| Setting | Default | Description |
|---------|---------|-------------|
| `check_interval_seconds` | 60 | How often the bot checks for new signals |
| `demo_mode` | `true` | **`true` = no real orders; `false` = real money trading** |
| `close_on_shutdown` | `false` | Close all positions when bot stops |
| `state_export_interval_seconds` | 5 | How often state.json is refreshed for dashboard |

---

## Dashboard

The web dashboard is served on **port 3000** and auto-refreshes every 10 seconds. It shows:

- **Bot Status** — running/stopped/error, uptime, last signal
- **Account Info** — balance, equity, margin, currency
- **P&L Summary** — total P&L, trade count, win rate, profit factor
- **Open Positions** — ticket, symbol, type, volume, entry, SL, TP, current P&L
- **Trade History** — last 50 trades (entries, exits, profits)
- **Errors & Warnings** — connection issues, failed orders, config problems

The dashboard reads from `state.json` (exported by the bot), so it does not need MT5 installed.

### Dashboard Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_STATE_FILE` | `/home/team/shared/bot/state.json` | Path to the bot's state JSON file |
| `DASHBOARD_PORT` | `3000` | Port the dashboard listens on |
| `DASHBOARD_HOST` | `0.0.0.0` | Bind address |
| `STALE_SECONDS` | `60` | Seconds before data is considered stale |

---

## Dry-Run vs Live Mode

| Mode | Flag | What it does |
|------|------|-------------|
| **Dry-Run** | `--dry-run` | Bot connects to MT5 (if available), computes signals, logs what it *would* do — **no real orders**. Safe for testing and development. |
| **Live** | `--live` | Bot sends real market orders to your MT5 account. **Uses real money.** Only use after thorough testing in dry-run mode. |

The `demo_mode` setting in `config.yaml` is the authoritative toggle. `--dry-run` / `--live` simply override it at startup.

> ⚠️ **WARNING:** `live` mode with `demo_mode: false` will trade with **real money**. Always test your strategy extensively in dry-run mode first.

---

## File Structure

```
bot/
├── bot.py                # Main entry point — bot loop and CLI
├── config.yaml           # All user-editable settings
├── executor.py           # Order placement, position sizing, risk management
├── mt5_connector.py      # MT5 connection, data fetching, account info
├── strategy.py           # MA crossover signal computation
├── state.py              # Thread-safe shared state + JSON export
├── state.json            # Live state snapshot (read by dashboard)
├── requirements.txt      # Python dependencies
├── start.sh              # Master launcher (dashboard + bot)
├── .env.example          # Dashboard environment variables reference
├── .gitignore            # Git ignore rules
├── dashboard/
│   ├── app.py            # Flask web dashboard
│   └── run_dashboard.sh  # Standalone dashboard launcher
└── logs/
    └── bot.log           # Bot runtime logs (rotating)
```

---

## Troubleshooting

### "MetaTrader5 Python package not available"
The bot runs without MT5 — install the MT5 terminal on Windows or via Wine, then ensure the `MetaTrader5` Python package is installed:
```bash
pip install MetaTrader5
```

### "MT5 initialisation failed"
- Make sure the MT5 terminal is running (or the `path` in `config.yaml` points to the correct executable)
- On Linux/Wine: start MT5 before running the bot, leave `path` empty
- Check your firewall isn't blocking MT5 connections

### "MT5 login failed"
- Double-check login, password, and server in `config.yaml`
- Ensure the account is active (demo accounts expire)
- Try logging in manually through the MT5 terminal first

### "Failed to fetch rates"
- Ensure the symbol is available in your MT5 Market Watch
- The symbol might be spelled differently (e.g. `EURUSD` vs `EURUSDm`)
- Try right-clicking the symbol in MT5 and selecting "Show All"

### Dashboard shows "State file not found"
- The bot must be running for the dashboard to show live data
- Run `./start.sh` to start both the bot and dashboard
- Check that `BOT_STATE_FILE` points to the correct path

### Port 3000 already in use
```bash
sudo lsof -t -iTCP:3000 -sTCP:LISTEN | xargs -r kill
```
Then restart the dashboard.

### Bot exits immediately with "Cannot connect to MT5 in live mode"
You have `demo_mode: false` but MT5 isn't connected. Either:
- Connect MT5 first and verify credentials
- Set `demo_mode: true` for dry-run testing without MT5
