"""
Dual-session opening range breakout bot.

Sessions (Amsterdam/CEST):
  EU  — VUSA on AEB,   09:00 candle, watches 09:05–10:30, capital €500
  US  — PLTR on SMART, 15:30 candle, watches 15:35–17:00, capital $500

Run via Windows Task Scheduler:
  08:55  →  python strategy.py --session eu
  15:25  →  python strategy.py --session us

Full-day run (dev/testing):
  python strategy.py
"""

import argparse
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from ibkr_connector import (
    connect, get_contract,
    get_opening_range_bar, get_latest_closed_1min_bar, get_rvol,
    place_bracket_order, cancel_order, close_position_at_market,
)
from telegram_notify import send_message

NL         = ZoneInfo("Europe/Amsterdam")
STATE_PATH = os.path.join(os.path.dirname(__file__), config.STATE_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    today = datetime.now(NL).strftime("%Y-%m-%d")
    state = {"date": today, "eu_trade_taken": False, "us_trade_taken": False}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            saved = json.load(f)
        if saved.get("date") == today:
            state.update(saved)
    return state


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Timing helpers
# ─────────────────────────────────────────────────────────────────────────────

def today_at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.now(NL).replace(hour=h, minute=m, second=0, microsecond=0)


def wait_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(NL)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))


# ─────────────────────────────────────────────────────────────────────────────
# Trade monitoring
# ─────────────────────────────────────────────────────────────────────────────

def monitor_bracket(ib, contract, trades, direction: str,
                    entry_price: float, take_profit: float, stop_loss: float,
                    breakout_level: float, qty: int,
                    symbol: str, currency: str, session_end: datetime) -> None:
    """Monitor open bracket order every 5s until TP/SL, range re-entry, or session end."""
    parent_trade, tp_trade, sl_trade = trades

    while datetime.now(NL) < session_end:
        ib.sleep(5)

        # ── TP hit ──────────────────────────────────────────────────────────
        if tp_trade.orderStatus.status == "Filled":
            fill   = tp_trade.orderStatus.avgFillPrice
            profit = abs(fill - entry_price) * qty
            send_message(f"🎯 TP hit! +{profit:.2f} {currency} profit  [{symbol}]")
            return

        # ── SL hit ──────────────────────────────────────────────────────────
        if sl_trade.orderStatus.status == "Filled":
            fill = sl_trade.orderStatus.avgFillPrice
            loss = abs(fill - entry_price) * qty
            send_message(f"❌ SL hit. -{loss:.2f} {currency} loss  [{symbol}]")
            return

        # ── Price re-entered the opening range — exit immediately ───────────
        bar = get_latest_closed_1min_bar(ib, contract)
        if bar is not None:
            re_entered = (bar.close < breakout_level if direction == "long"
                          else bar.close > breakout_level)
            if re_entered:
                cancel_order(ib, tp_trade)
                cancel_order(ib, sl_trade)
                ib.sleep(1)
                close_position_at_market(ib, contract, direction, qty)
                send_message(
                    f"🚪 {symbol} price re-entered range — exited at market\n"
                    f"   breakout level was {breakout_level:.2f}"
                )
                return

    # ── Session end — close any open position ────────────────────────────────
    cancel_order(ib, tp_trade)
    cancel_order(ib, sl_trade)
    ib.sleep(1)
    close_position_at_market(ib, contract, direction, qty)
    send_message(f"⏰ {symbol} session ended — position closed at market")


# ─────────────────────────────────────────────────────────────────────────────
# Main session runner
# ─────────────────────────────────────────────────────────────────────────────

def run_session(session) -> None:
    state     = load_state()
    state_key = f"{session.symbol.lower()}_trade_taken"
    session_end = today_at(session.session_end)

    if datetime.now(NL) >= session_end:
        print(f"{session.symbol}: session window already closed — skipping.")
        return

    if state.get(state_key):
        print(f"{session.symbol}: trade already taken today — skipping.")
        return

    # ── Connect ──────────────────────────────────────────────────────────────
    print(f"\n{'='*55}\n  {session.symbol} session  |  window: "
          f"{session.opening_end}–{session.session_end} NL\n{'='*55}")
    send_message(
        f"🔔 {'EU' if session.opening_start < '12:00' else 'US'} session starting "
        f"— watching {session.symbol} {session.opening_start} candle"
    )

    ib       = connect()
    contract = get_contract(session.symbol, session.exchange, session.currency)

    # ── Wait for opening candle to close ─────────────────────────────────────
    wait_until(today_at(session.opening_end))

    opening_bar = None
    for _ in range(24):   # retry up to 2 min for IBKR data delay
        opening_bar = get_opening_range_bar(ib, contract, session.opening_start)
        if opening_bar is not None:
            break
        ib.sleep(5)

    if opening_bar is None:
        msg = f"⚠️ {session.symbol}: opening candle not available — aborting session"
        print(msg)
        send_message(msg)
        ib.disconnect()
        return

    opening_high = opening_bar.high
    opening_low  = opening_bar.low
    print(f"  Opening range: {opening_low:.2f} – {opening_high:.2f} {session.currency}")
    send_message(
        f"📊 {session.symbol} range drawn: "
        f"{opening_low:.2f} – {opening_high:.2f} {session.currency}"
    )

    # ── Poll for breakout → retest ────────────────────────────────────────────
    direction      = None
    breakout_level = None

    while datetime.now(NL) < session_end:
        bar = get_latest_closed_1min_bar(ib, contract)
        if bar is None:
            ib.sleep(config.POLL_INTERVAL_SEC)
            continue

        bar_time = bar.date.astimezone(NL).strftime("%H:%M")
        print(f"  {bar_time}  O:{bar.open:.2f}  H:{bar.high:.2f}  "
              f"L:{bar.low:.2f}  C:{bar.close:.2f}  Vol:{bar.volume}")

        # ── Breakout detection ────────────────────────────────────────────────
        if direction is None:
            if bar.close > opening_high:
                rvol = get_rvol(ib, contract, bar.volume)
                if rvol < config.RVOL_MIN:
                    print(f"  → Breakout above {opening_high:.2f} but RVOL={rvol:.2f} "
                          f"< {config.RVOL_MIN} — skipping")
                    send_message(
                        f"⚠️ {session.symbol} breakout above {opening_high:.2f} "
                        f"ignored — low volume (RVOL {rvol:.1f}x)"
                    )
                    ib.sleep(config.POLL_INTERVAL_SEC)
                    continue
                direction      = "long"
                breakout_level = opening_high
                print(f"  → BREAKOUT LONG above {breakout_level:.2f}  RVOL={rvol:.2f}x")
                send_message(
                    f"📈 {session.symbol} broke above {breakout_level:.2f} "
                    f"— watching for retest  (RVOL {rvol:.1f}x)"
                )

            elif bar.close < opening_low:
                rvol = get_rvol(ib, contract, bar.volume)
                if rvol < config.RVOL_MIN:
                    print(f"  → Breakout below {opening_low:.2f} but RVOL={rvol:.2f} "
                          f"< {config.RVOL_MIN} — skipping")
                    send_message(
                        f"⚠️ {session.symbol} breakout below {opening_low:.2f} "
                        f"ignored — low volume (RVOL {rvol:.1f}x)"
                    )
                    ib.sleep(config.POLL_INTERVAL_SEC)
                    continue
                direction      = "short"
                breakout_level = opening_low
                print(f"  → BREAKOUT SHORT below {breakout_level:.2f}  RVOL={rvol:.2f}x")
                send_message(
                    f"📉 {session.symbol} broke below {breakout_level:.2f} "
                    f"— watching for retest  (RVOL {rvol:.1f}x)"
                )

        # ── Retest detection ──────────────────────────────────────────────────
        else:
            if direction == "long":
                touched   = bar.low  <= breakout_level + config.RETEST_TOLERANCE
                rejection = bar.close > breakout_level   # closes back above level ✅
                failure   = bar.close < opening_low      # closes back inside range ❌
            else:
                touched   = bar.high >= breakout_level - config.RETEST_TOLERANCE
                rejection = bar.close < breakout_level   # closes back below level ✅
                failure   = bar.close > opening_high     # closes back inside range ❌

            if touched and failure:
                print(f"  → FAILED RETEST — price closed back inside range")
                send_message(f"⚠️ Failed retest detected — skipping today  [{session.symbol}]")
                break

            if touched and rejection:
                entry_price = breakout_level
                qty         = max(1, int(session.capital / entry_price))

                if direction == "long":
                    action      = "BUY"
                    stop_loss   = opening_low
                    risk        = entry_price - stop_loss
                    take_profit = entry_price + config.RISK_REWARD_RATIO * risk
                else:
                    action      = "SELL"
                    stop_loss   = opening_high
                    risk        = stop_loss - entry_price
                    take_profit = entry_price - config.RISK_REWARD_RATIO * risk

                trades = place_bracket_order(
                    ib, contract, action, qty,
                    entry_price, take_profit, stop_loss
                )

                side = "Long" if direction == "long" else "Short"
                send_message(
                    f"✅ {side} {session.symbol} at {entry_price:.2f}, "
                    f"SL {stop_loss:.2f}, TP {take_profit:.2f}  "
                    f"(qty {qty}, exposure {entry_price * qty:.0f} {session.currency})"
                )

                state[state_key] = True
                save_state(state)

                monitor_bracket(
                    ib, contract, trades,
                    direction, entry_price, take_profit, stop_loss,
                    breakout_level, qty,
                    session.symbol, session.currency, session_end
                )
                ib.disconnect()
                return

        ib.sleep(config.POLL_INTERVAL_SEC)

    # ── Session ended without a trade ─────────────────────────────────────────
    send_message(f"😴 No clean setup today — window closed  [{session.symbol}]")
    ib.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Opening range breakout bot")
    parser.add_argument(
        "--session", choices=["eu", "us"],
        help="Run a single session (eu or us). Omit to run both sequentially."
    )
    args = parser.parse_args()

    if args.session == "eu":
        run_session(config.EU_SESSION)
    elif args.session == "us":
        run_session(config.US_SESSION)
    else:
        run_session(config.EU_SESSION)
        run_session(config.US_SESSION)


if __name__ == "__main__":
    main()
