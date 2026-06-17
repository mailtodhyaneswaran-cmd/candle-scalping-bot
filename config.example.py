"""
Copy this file to config.py and fill in your credentials before running.
config.py is excluded from git to keep credentials private.
"""
from types import SimpleNamespace
from zoneinfo import ZoneInfo

NL = ZoneInfo("Europe/Amsterdam")

# ── IBKR connection ──────────────────────────────────────────────────────────
IBKR_HOST     = "127.0.0.1"
LIVE_TRADING  = False
IBKR_PORT     = 7496 if LIVE_TRADING else 7497   # TWS: 7497 paper / 7496 live
IBKR_CLIENT_ID = 35

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID"

# ── Strategy parameters ──────────────────────────────────────────────────────
# FIX: was RETEST_TOLERANCE = 0.05 (absolute — meaningless across price levels)
RETEST_TOLERANCE_PCT = 0.0005   # 5 bps of mid-range price (e.g. 37¢ on SPY@750)

# NEW: skip days whose opening range is too thin for 2R to clear costs
MIN_RANGE_PCT        = 0.0015   # 0.15% of price (e.g. $1.13 on SPY@750)

RISK_REWARD_RATIO    = 2        # take profit = entry ± (RR × risk)
POLL_INTERVAL_SEC    = 60       # seconds between 1-min bar polls
RVOL_MIN             = 1.5      # minimum relative volume for a valid breakout

# ── Sessions (all times in Netherlands/Amsterdam local time, CEST) ────────────
EU_SESSION = SimpleNamespace(
    name          = "eu",           # FIX: dedup key — do NOT change this
    symbol        = "ASML",         # was VUSA — a passive US tracker with no EU open discovery
    exchange      = "AEB",
    currency      = "EUR",
    opening_start = "09:00",
    opening_end   = "09:05",
    session_end   = "10:30",
    capital       = 1000,           # ASML ~€600–900; €500 can't fit 1 share
)

US_SESSION = SimpleNamespace(
    name          = "us",           # FIX: dedup key — do NOT change this
    symbol        = "PLTR",
    exchange      = "SMART",
    currency      = "USD",
    opening_start = "15:30",
    opening_end   = "15:35",
    session_end   = "17:00",
    capital       = 500,
)

# ── Files ─────────────────────────────────────────────────────────────────────
STATE_FILE = "state.json"