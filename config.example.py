"""
Copy this file to config.py and fill in your credentials before running.
config.py is excluded from git to keep credentials private.
"""

from types import SimpleNamespace
from zoneinfo import ZoneInfo

NL = ZoneInfo("Europe/Amsterdam")

# ── IBKR connection ──────────────────────────────────────────────────────────
IBKR_HOST = "127.0.0.1"
LIVE_TRADING = False
IBKR_PORT = 7496 if LIVE_TRADING else 7497   # TWS: 7497 paper / 7496 live
IBKR_CLIENT_ID = 35

# ── Telegram ──────────────────────────────────────────────────────────────────
# Get token from @BotFather on Telegram.
# Get chat_id by messaging your bot then visiting:
#   https://api.telegram.org/bot<TOKEN>/getUpdates
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID"

# ── Strategy parameters ──────────────────────────────────────────────────────
RETEST_TOLERANCE  = 0.05   # price must come within this of the broken level
RISK_REWARD_RATIO = 2      # take profit = entry +/- (RR * risk)
POLL_INTERVAL_SEC = 60     # seconds between 1-min bar polls
RVOL_MIN          = 1.5    # minimum relative volume for a valid breakout

# ── Sessions (all times in Netherlands/Amsterdam local time, CEST) ────────────
EU_SESSION = SimpleNamespace(
    symbol        = "VUSA",    # Vanguard S&P 500 UCITS ETF — Euronext Amsterdam
    exchange      = "AEB",     # Alternative: EXS1 on XETRA (exchange="XETRA")
    currency      = "EUR",
    opening_start = "09:00",
    opening_end   = "09:05",
    session_end   = "10:30",
    capital       = 500,       # max EUR exposure per session
)

US_SESSION = SimpleNamespace(
    symbol        = "PLTR",    # Palantir — high intraday volatility
    exchange      = "SMART",   # Alternative: TSLA (exchange="SMART", currency="USD")
    currency      = "USD",
    opening_start = "15:30",   # = 9:30 AM ET
    opening_end   = "15:35",   # = 9:35 AM ET
    session_end   = "17:00",   # = 11:00 AM ET
    capital       = 500,       # max USD exposure per session
)

# ── Files ─────────────────────────────────────────────────────────────────────
STATE_FILE = "state.json"
