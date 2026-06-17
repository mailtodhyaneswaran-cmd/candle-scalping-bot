"""
Pre-flight test for strategy.py — run this the evening before or right now
to verify everything works, simulate the full trade cycle, and watch live
market data being polled every minute.

Sections:
  1. IBKR connection
  2. Telegram notification
  3. Contract qualification + opening bar fetch (VUSA & TSLA)
  4. Full historical simulation — opening range → breakout → retest → SL/TP outcome
  5. Live polling demo — real data fetched from IBKR every 60s
  6. State file reset for tomorrow
"""

import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from ibkr_connector import connect, get_contract
from telegram_notify import send_message

NL = ZoneInfo("Europe/Amsterdam")

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"


def ts() -> str:
    return datetime.now(NL).strftime("%H:%M:%S")


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def fetch_bars(ib, contract, size, duration):
    return ib.reqHistoricalData(
        contract, endDateTime="", durationStr=duration,
        barSizeSetting=size, whatToShow="TRADES",
        useRTH=True, formatDate=1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. IBKR connection
# ─────────────────────────────────────────────────────────────────────────────

def test_connection():
    section("1. IBKR Connection")
    try:
        ib = connect()
        if not ib.isConnected():
            print(f"  {FAIL} Cannot connect to IB Gateway at "
                  f"{config.IBKR_HOST}:{config.IBKR_PORT}")
            return None
        accounts = ib.managedAccounts()
        print(f"  {PASS} Connected  |  Account: {accounts}")
        return ib
    except Exception as e:
        print(f"  {FAIL} {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Telegram
# ─────────────────────────────────────────────────────────────────────────────

def test_telegram():
    section("2. Telegram")
    send_message("🔧 Pre-flight test running — bot is ready for tomorrow")
    print(f"  {PASS} Message sent — check your phone")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Contract + opening bar
# ─────────────────────────────────────────────────────────────────────────────

def test_contract(ib, session) -> bool:
    section(f"3. Contract: {session.symbol} ({session.exchange}, {session.currency})")
    try:
        contract = get_contract(session.symbol, session.exchange, session.currency)
        ib.qualifyContracts(contract)
        print(f"  {PASS} Qualified: {contract}")
    except Exception as e:
        print(f"  {FAIL} {e}")
        return False

    bars = fetch_bars(ib, contract, "5 mins", "5 D")
    if not bars:
        print(f"  {FAIL} No historical bars returned")
        return False

    opening_bar = next(
        (b for b in reversed(bars)
         if b.date.astimezone(NL).strftime("%H:%M") == session.opening_start),
        None
    )
    if opening_bar is None:
        print(f"  {FAIL} No bar found at {session.opening_start} NL in recent data")
        print(f"  {INFO} Last 5 bar times: "
              f"{[b.date.astimezone(NL).strftime('%H:%M') for b in bars[-5:]]}")
        return False

    d = opening_bar.date.astimezone(NL).strftime("%Y-%m-%d")
    print(f"  {PASS} Opening bar ({d} {session.opening_start} NL)  "
          f"O:{opening_bar.open}  H:{opening_bar.high}  "
          f"L:{opening_bar.low}  C:{opening_bar.close}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 4. Full historical simulation including SL/TP outcome
# ─────────────────────────────────────────────────────────────────────────────

def simulate_session(ib, session) -> None:
    section(f"4. Historical simulation — {session.symbol}")

    contract = get_contract(session.symbol, session.exchange, session.currency)

    # Find the most recent opening bar
    bars_5m = fetch_bars(ib, contract, "5 mins", "5 D")
    opening_bar = next(
        (b for b in reversed(bars_5m)
         if b.date.astimezone(NL).strftime("%H:%M") == session.opening_start),
        None
    )
    if opening_bar is None:
        print(f"  {FAIL} No opening bar — cannot simulate")
        return

    opening_date  = opening_bar.date.astimezone(NL).strftime("%Y-%m-%d")
    opening_high  = opening_bar.high
    opening_low   = opening_bar.low
    print(f"  {INFO} Date: {opening_date}")
    print(f"  {INFO} Opening range  Low: {opening_low:.2f}  High: {opening_high:.2f}  "
          f"({session.currency})")

    # Get all 1-min bars for that session window
    bars_1m = fetch_bars(ib, contract, "1 min", "5 D")
    session_bars = [
        b for b in bars_1m
        if b.date.astimezone(NL).strftime("%Y-%m-%d") == opening_date
        and session.opening_end
            <= b.date.astimezone(NL).strftime("%H:%M")
            < session.session_end
    ]

    if not session_bars:
        print(f"  {FAIL} No 1-min bars found for session window")
        return

    print(f"  {INFO} Replaying {len(session_bars)} 1-min bars "
          f"({session.opening_end}–{session.session_end} NL)\n")
    print(f"  {'Time':<7} {'O':>8} {'H':>8} {'L':>8} {'C':>8}  {'Signal'}")
    print(f"  {'-'*65}")

    direction      = None
    breakout_level = None
    entry_price    = None
    take_profit    = None
    stop_loss      = None
    qty            = 0
    in_trade       = False

    for bar in session_bars:
        bar_time = bar.date.astimezone(NL).strftime("%H:%M")
        signal   = ""

        if not in_trade:
            if direction is None:
                if bar.close > opening_high:
                    direction      = "long"
                    breakout_level = opening_high
                    signal = f"⬆ BREAKOUT LONG above {breakout_level:.2f}"
                elif bar.close < opening_low:
                    direction      = "short"
                    breakout_level = opening_low
                    signal = f"⬇ BREAKOUT SHORT below {breakout_level:.2f}"
            else:
                if direction == "long":
                    touched   = bar.low  <= breakout_level + config.RETEST_TOLERANCE
                    rejection = bar.close > breakout_level
                else:
                    touched   = bar.high >= breakout_level - config.RETEST_TOLERANCE
                    rejection = bar.close < breakout_level

                if touched and rejection:
                    entry_price = breakout_level
                    qty         = max(1, int(session.capital / entry_price))
                    if direction == "long":
                        stop_loss   = opening_low
                        risk        = entry_price - stop_loss
                        take_profit = entry_price + config.RISK_REWARD_RATIO * risk
                    else:
                        stop_loss   = opening_high
                        risk        = stop_loss - entry_price
                        take_profit = entry_price - config.RISK_REWARD_RATIO * risk

                    in_trade = True
                    signal = (f"✅ RETEST → ORDER  entry={entry_price:.2f}  "
                              f"SL={stop_loss:.2f}  TP={take_profit:.2f}  qty={qty}")
        else:
            # Check SL and TP against bar's range
            tp_hit = (bar.high >= take_profit) if direction == "long" else (bar.low <= take_profit)
            sl_hit = (bar.low  <= stop_loss)   if direction == "long" else (bar.high >= stop_loss)

            if sl_hit and tp_hit:
                signal = "⚠ BOTH SL and TP in range — ambiguous bar"
            elif tp_hit:
                pnl    = (take_profit - entry_price) * qty if direction == "long" \
                         else (entry_price - take_profit) * qty
                signal = f"🎯 TP HIT @ {take_profit:.2f} — profit: +{pnl:.2f} {session.currency}"
                print(f"  {bar_time:<7} {bar.open:>8.2f} {bar.high:>8.2f} "
                      f"{bar.low:>8.2f} {bar.close:>8.2f}  {signal}")
                break
            elif sl_hit:
                pnl    = (stop_loss - entry_price) * qty if direction == "long" \
                         else (entry_price - stop_loss) * qty
                signal = f"❌ SL HIT @ {stop_loss:.2f} — loss: {pnl:.2f} {session.currency}"
                print(f"  {bar_time:<7} {bar.open:>8.2f} {bar.high:>8.2f} "
                      f"{bar.low:>8.2f} {bar.close:>8.2f}  {signal}")
                break

        print(f"  {bar_time:<7} {bar.open:>8.2f} {bar.high:>8.2f} "
              f"{bar.low:>8.2f} {bar.close:>8.2f}  {signal}")

    else:
        if in_trade:
            print(f"\n  {INFO} Session ended with order still open — "
                  f"bot would send Telegram alert to close manually")
        elif direction:
            print(f"\n  {INFO} Breakout detected but no retest — no trade taken")
        else:
            print(f"\n  {INFO} Price stayed inside opening range — no trade taken")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Live polling demo
# ─────────────────────────────────────────────────────────────────────────────

def live_poll_demo(ib, session, duration_mins: int = 5) -> None:
    section(f"5. Live polling demo — {session.symbol}  ({duration_mins} min)")
    print(f"  Fetching a new 1-min bar from IBKR every 60s.")
    print(f"  This is exactly what strategy.py does during the session.\n")

    contract = get_contract(session.symbol, session.exchange, session.currency)
    ib.qualifyContracts(contract)

    # Try to get today's opening range for context
    bars_5m = fetch_bars(ib, contract, "5 mins", "1 D")
    opening_bar = next(
        (b for b in reversed(bars_5m)
         if b.date.astimezone(NL).strftime("%H:%M") == session.opening_start),
        None
    )
    if opening_bar:
        print(f"  {INFO} Today's opening range  "
              f"Low: {opening_bar.low:.2f}  High: {opening_bar.high:.2f}  "
              f"({session.currency})")
        opening_high = opening_bar.high
        opening_low  = opening_bar.low
    else:
        print(f"  {INFO} Opening range not available yet (market may not have opened today)")
        opening_high = opening_low = None

    print(f"\n  {'Time':<10} {'O':>8} {'H':>8} {'L':>8} {'C':>8} {'Vol':>8}  Status")
    print(f"  {'-'*70}")

    end_time = time.time() + duration_mins * 60
    poll     = 0

    while time.time() < end_time:
        poll += 1
        bars = fetch_bars(ib, contract, "1 min", "1800 S")

        if len(bars) < 2:
            print(f"  {ts()}  No bars returned — market may be closed")
        else:
            bar = bars[-2]  # last fully closed bar
            bar_time = bar.date.astimezone(NL).strftime("%H:%M")

            status = ""
            if opening_high and opening_low:
                if bar.close > opening_high:
                    status = f"above range high ({opening_high:.2f}) ⬆"
                elif bar.close < opening_low:
                    status = f"below range low ({opening_low:.2f}) ⬇"
                else:
                    status = f"inside range [{opening_low:.2f}–{opening_high:.2f}]"

            print(f"  {ts():<10} {bar.open:>8.2f} {bar.high:>8.2f} "
                  f"{bar.low:>8.2f} {bar.close:>8.2f} {bar.volume:>8}  "
                  f"bar@{bar_time}  {status}")

        remaining = int(end_time - time.time())
        if remaining > 0:
            print(f"  ... next poll in 60s  ({remaining}s remaining in demo) ...")
            time.sleep(min(60, remaining))

    print(f"\n  {PASS} Live polling demo complete — strategy.py does this continuously")


# ─────────────────────────────────────────────────────────────────────────────
# 6. State reset
# ─────────────────────────────────────────────────────────────────────────────

def reset_state():
    section("6. State reset")
    state_path = os.path.join(os.path.dirname(__file__), config.STATE_FILE)
    today = datetime.now(NL).strftime("%Y-%m-%d")
    state = {"date": today, "eu_trade_taken": False, "us_trade_taken": False}
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
    print(f"  {PASS} state.json reset for {today} — both sessions will trade tomorrow")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  STRATEGY PRE-FLIGHT TEST")
    print("="*60)

    ib = test_connection()
    if ib is None:
        print("\nAborting — fix IB Gateway connection first.")
        return

    test_telegram()

    eu_ok = test_contract(ib, config.EU_SESSION)
    us_ok = test_contract(ib, config.US_SESSION)

    if eu_ok:
        simulate_session(ib, config.EU_SESSION)
    if us_ok:
        simulate_session(ib, config.US_SESSION)

    # Live poll whichever session is currently in market hours
    now_hhmm = datetime.now(NL).strftime("%H:%M")
    if config.EU_SESSION.opening_end <= now_hhmm < config.EU_SESSION.session_end and eu_ok:
        live_poll_demo(ib, config.EU_SESSION, duration_mins=3)
    elif config.US_SESSION.opening_end <= now_hhmm < config.US_SESSION.session_end and us_ok:
        live_poll_demo(ib, config.US_SESSION, duration_mins=3)
    else:
        print(f"\n  {INFO} No session active right now ({now_hhmm} NL) — "
              f"skipping live poll demo")
        print(f"  {INFO} Run between 09:05–11:00 or 15:35–17:00 NL to see live polling")

    reset_state()

    ib.disconnect()

    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)
    print(f"  EU session (VUSA): {'ready ✓' if eu_ok else 'issues found ✗'}")
    print(f"  US session (TSLA): {'ready ✓' if us_ok else 'issues found ✗'}")
    print(f"\n  Tomorrow: start  python strategy.py  before 09:00 NL time")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
