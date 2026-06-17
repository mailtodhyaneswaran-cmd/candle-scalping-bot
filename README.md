# Opening Range Breakout Bot

Automated intraday scalping bot that trades the first 5-minute candle breakout on two daily sessions — European (VUSA) and US (PLTR) — via Interactive Brokers.

---

## Strategy

1. Wait for the first 5-min candle to close after market open
2. Record its high and low as the **opening range**
3. Poll 1-min candles every 60 seconds
4. When a candle closes outside the range — **breakout confirmed** (only if RVOL ≥ 1.5×)
5. Wait for price to retest the broken level
6. If the retest candle closes back on the breakout side — **enter with a bracket order**
7. Exit when TP hits (2R), SL hits, price re-enters range, or session window closes

One trade per session per day maximum.

---

## Sessions

| Session | Time (CEST) | Symbol | Exchange |
|---------|-------------|--------|----------|
| European | 09:00 – 10:30 | VUSA | Euronext Amsterdam (AEB) |
| US | 15:30 – 17:00 | PLTR | SMART (NASDAQ) |

---

## Prerequisites

- Python 3.10+ (tested on 3.14)
- Interactive Brokers account (paper trading recommended to start)
- TWS (Trader Workstation) or IB Gateway installed and running
- Telegram bot for notifications

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure TWS / IB Gateway

- Log into your **paper trading** account
- Go to `Edit → Global Configuration → API → Settings`
- Enable **ActiveX and Socket Clients**
- Set socket port to `7497` (paper) or `7496` (live)
- Uncheck **Read-Only API**

### 3. Create your config file

```bash
cp config.example.py config.py
```

Open `config.py` and fill in:

| Field | Where to get it |
|-------|----------------|
| `TELEGRAM_BOT_TOKEN` | Create a bot via [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Message your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `IBKR_CLIENT_ID` | Any unused integer (default: 35) |
| `LIVE_TRADING` | Keep `False` until validated on paper |

### 4. Run the pre-flight test

Checks connection, Telegram, bar fetching, and replays yesterday's session:

```bash
python test_strategy.py
```

---

## Running

### Manual (both sessions)

```bash
python strategy.py
```

Runs EU session first, then waits and runs US session. Start before 09:00 CEST.

### Single session

```bash
python strategy.py --session eu    # European session only
python strategy.py --session us    # US session only
```

### Windows Task Scheduler (recommended)

Two tasks are pre-configured in the project:

| Task name | Trigger | Script |
|-----------|---------|--------|
| `CandleBot_EU` | 08:55 Mon–Fri | `run_eu.bat` |
| `CandleBot_US` | 15:25 Mon–Fri | `run_us.bat` |

Re-register them after cloning:

```powershell
schtasks /create /tn "CandleBot_EU" /tr "C:\path\to\run_eu.bat" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 08:55 /f
schtasks /create /tn "CandleBot_US" /tr "C:\path\to\run_us.bat" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 15:25 /f
```

---

## Telegram notifications

| Event | Message |
|-------|---------|
| Session starting | `🔔 EU session starting — watching VUSA 9:00 candle` |
| Breakout detected | `📈 VUSA broke above 101.50 — watching for retest (RVOL 2.1x)` |
| Low volume breakout | `⚠️ Breakout ignored — low volume (RVOL 0.9x)` |
| Failed retest | `⚠️ Failed retest detected — skipping today` |
| Order placed | `✅ Long VUSA at 101.50, SL 100.80, TP 102.90 (qty 4, exposure 406 EUR)` |
| TP hit | `🎯 TP hit! +4.80 EUR profit [VUSA]` |
| SL hit | `❌ SL hit. -2.80 EUR loss [VUSA]` |
| Price re-entered range | `🚪 VUSA price re-entered range — exited at market` |
| No setup | `😴 No clean setup today — window closed [VUSA]` |

---

## Files

| File | Purpose |
|------|---------|
| `strategy.py` | Main bot — entry point |
| `ibkr_connector.py` | IBKR API wrapper (ib_async) |
| `telegram_notify.py` | Telegram notification helper |
| `config.example.py` | Config template — copy to `config.py` |
| `test_strategy.py` | Pre-flight test + historical dry-run |
| `test_connection.py` | IBKR connection + bracket order tester |
| `run_eu.bat` | Launcher for EU session (Task Scheduler) |
| `run_us.bat` | Launcher for US session (Task Scheduler) |
| `state.json` | Runtime — tracks daily trade status (auto-created) |
| `log_eu.txt` | Runtime — EU session log (auto-created) |
| `log_us.txt` | Runtime — US session log (auto-created) |

---

## Switching to live trading

1. Set `LIVE_TRADING = True` in `config.py`
2. Log TWS into your **live** account (port switches to 7496 automatically)
3. Test with 1 share first (`capital = price_of_1_share`)
