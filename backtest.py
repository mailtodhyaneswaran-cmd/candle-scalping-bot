"""
Multi-day, multi-symbol ORB backtester.

Uses strategy_core.simulate_session — the exact same logic as the live bot.
RVOL default is "rolling" which mirrors live get_rvol (last 10 bars),
fixing the SPY / high-volume-open problem where "opening" baseline
was always suppressing valid breakout signals.

Usage:
  python backtest.py --days 60 --us --symbols SPY:SMART:USD PLTR:SMART:USD
  python backtest.py --days 60 --symbols ASML:AEB:EUR EXS1:IBIS:EUR VUSA:AEB:EUR
  python backtest.py --csv ./data --symbols ASML:AEB:EUR
  python backtest.py --days 10 --us --symbols SPY:SMART:USD --rvol-mode disabled  # diagnosis
"""
from __future__ import annotations
import argparse
import csv
import os
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_core import Bar, Params, TradeResult, simulate_session

NL = ZoneInfo("Europe/Amsterdam")

EU_WINDOW = ("09:00", "09:05", "10:30")
US_WINDOW = ("15:30", "15:35", "17:00")


# ─────────────────────────────────────────────────────────────────
# Symbol spec
# ─────────────────────────────────────────────────────────────────
class SymbolSpec:
    def __init__(self, symbol, exchange, currency):
        self.symbol   = symbol
        self.exchange = exchange
        self.currency = currency

    @classmethod
    def parse(cls, s: str) -> "SymbolSpec":
        sym, exch, cur = s.split(":")
        return cls(sym, exch, cur)


# ─────────────────────────────────────────────────────────────────
# Data sources
# ─────────────────────────────────────────────────────────────────
def _hhmm(s):
    h, m = map(int, s.split(":")); return h, m


def _window_bounds(day: datetime, window):
    (sh, sm), (_, _), (eh, em) = (_hhmm(window[0]), _hhmm(window[1]), _hhmm(window[2]))
    start = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end   = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start, end


def fetch_ibkr(spec: SymbolSpec, days: int, window) -> dict:
    """Returns {date_str: [Bar, ...]} for up to `days` trading days."""
    from ib_async import IB, Stock
    print(f"\n{'─'*55}")
    print(f"  {spec.symbol}  ({spec.exchange} / {spec.currency})")
    print(f"{'─'*55}")
    print(f"  Connecting to IBKR (TWS port 7497)...")
    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=77)
    print(f"  Connected. Qualifying contract {spec.symbol} on {spec.exchange}...")
    contract = Stock(spec.symbol, spec.exchange, spec.currency)
    ib.qualifyContracts(contract)
    print(f"  Contract OK. Fetching {days} trading days of 1-min bars...")

    out   = {}
    today = datetime.now(NL)
    back  = 0
    while len(out) < days and back < days * 3:
        day = today - timedelta(days=back)
        back += 1
        if day.weekday() >= 5:
            continue
        start, end = _window_bounds(day, window)
        bars = ib.reqHistoricalData(
            contract, endDateTime=end, durationStr="3600 S",
            barSizeSetting="1 min", whatToShow="TRADES",
            useRTH=False, formatDate=2,
        )
        rows = []
        for b in bars:
            t = b.date.astimezone(NL) if hasattr(b.date, "astimezone") else b.date
            if start <= t <= end:
                rows.append(Bar(t.strftime("%H:%M"), b.open, b.high, b.low, b.close, b.volume))
        if len(rows) >= 6:
            ds = day.strftime("%Y-%m-%d")
            out[ds] = rows
            print(f"  [{len(out):>3}/{days}] {ds} ... {len(rows)} bars")

    print(f"  Done. {len(out)} days fetched. Disconnecting...")
    ib.disconnect()
    return out


def load_csv(spec: SymbolSpec, directory: str) -> dict:
    out = {}
    for fn in sorted(os.listdir(directory)):
        if not fn.startswith(spec.symbol + "_") or not fn.endswith(".csv"):
            continue
        date_str = fn[len(spec.symbol) + 1:-4]
        rows = []
        with open(os.path.join(directory, fn)) as f:
            for r in csv.DictReader(f):
                rows.append(Bar(r["time"], float(r["open"]), float(r["high"]),
                                float(r["low"]), float(r["close"]), float(r["volume"])))
        if len(rows) >= 6:
            out[date_str] = rows
    return out


# ─────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────
def run_symbol(spec: SymbolSpec, days_bars: dict, capital: float,
               params: Params) -> list:
    """Simulate every day and return list of (date_str, TradeResult)."""
    print(f"\n  Simulating {len(days_bars)} days...")
    trades = []
    for date_str, rows in sorted(days_bars.items()):
        opening = rows[:5]       # 09:00–09:04  (first 5 one-min bars)
        post    = rows[5:]       # 09:05 onward
        if not post:
            continue

        oh = max(b.high for b in opening)
        ol = min(b.low  for b in opening)
        tr = simulate_session(opening, post, capital, params)

        if tr:
            trades.append((date_str, tr))
            print(f"    {date_str}  range {ol:.2f}–{oh:.2f}"
                  f"  → {tr.direction} entry {tr.entry:.2f}"
                  f"  {tr.exit_reason}  R={tr.r_multiple:.2f}"
                  f"  net={tr.net_pnl:.2f} {spec.currency}"
                  f"  rvol={tr.rvol_at_breakout:.1f}x")
        else:
            print(f"    {date_str}  range {ol:.2f}–{oh:.2f}  no setup")

    print(f"  → {len(trades)} trade(s) out of {len(days_bars)} days")
    return trades


# ─────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────
def summarize(spec: SymbolSpec, trades: list, n_days: int) -> dict:
    if not trades:
        return dict(symbol=spec.symbol, days=n_days, trades=0)
    rs   = [t.r_multiple for _, t in trades]
    nets = [t.net_pnl    for _, t in trades]
    wins = [t for _, t in trades if t.net_pnl > 0]
    gw   = sum(t.net_pnl for _, t in trades if t.net_pnl > 0)
    gl   = -sum(t.net_pnl for _, t in trades if t.net_pnl < 0)
    return dict(
        symbol=spec.symbol, days=n_days, trades=len(trades),
        win_pct=100 * len(wins) / len(trades),
        avg_R=statistics.mean(rs), total_R=sum(rs),
        net=sum(nets), profit_factor=(gw / gl if gl else float("inf")),
        tp      =sum(1 for _, t in trades if t.exit_reason == "tp"),
        sl      =sum(1 for _, t in trades if t.exit_reason == "sl"),
        reentry =sum(1 for _, t in trades if t.exit_reason == "range_reentry"),
        ses_end =sum(1 for _, t in trades if t.exit_reason == "session_end"),
    )


def print_table(rows: list):
    hdr = (f"{'symbol':8} {'days':>4} {'trades':>6} {'win%':>6} "
           f"{'avgR':>6} {'totR':>7} {'net':>9} {'PF':>5}  tp/sl/re/end")
    print("\n" + hdr)
    print("─" * len(hdr))
    for r in rows:
        if r["trades"] == 0:
            print(f"{r['symbol']:8} {r['days']:>4} {0:>6}"
                  f"      —      —       —         —      —   no setups")
            continue
        print(f"{r['symbol']:8} {r['days']:>4} {r['trades']:>6}"
              f" {r['win_pct']:>5.0f}%"
              f" {r['avg_R']:>6.2f} {r['total_R']:>7.1f}"
              f" {r['net']:>9.2f} {r['profit_factor']:>5.2f}"
              f"  {r['tp']}/{r['sl']}/{r['reentry']}/{r['ses_end']}")


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="ORB backtester")
    ap.add_argument("--days",     type=int,   default=60)
    ap.add_argument("--capital",  type=float, default=500)
    ap.add_argument("--csv",      default=None, help="directory of SYMBOL_DATE.csv files")
    ap.add_argument("--us",       action="store_true", help="use US window (15:30–17:00)")
    ap.add_argument("--rvol-mode", default="rolling",
                    choices=["rolling", "opening", "disabled"],
                    help="rolling=mirrors live get_rvol (default, fixes SPY); "
                         "opening=old behaviour; disabled=skip RVOL gate entirely")
    ap.add_argument("--symbols",  nargs="+",
                    default=["VUSA:AEB:EUR", "ASML:AEB:EUR", "EXS1:IBIS:EUR"])
    args = ap.parse_args()

    window = US_WINDOW if args.us else EU_WINDOW
    params = Params(rvol_mode=args.rvol_mode)

    if args.rvol_mode != "rolling":
        print(f"\n⚠  RVOL mode: {args.rvol_mode.upper()}"
              f" — results may diverge from live bot behaviour.")

    summary_rows = []
    for s in args.symbols:
        spec = SymbolSpec.parse(s)
        data = load_csv(spec, args.csv) if args.csv else fetch_ibkr(spec, args.days, window)
        trades = run_symbol(spec, data, args.capital, params)
        summary_rows.append(summarize(spec, trades, len(data)))

    print_table(summary_rows)
    print("\nRVOL mode:", args.rvol_mode,
          "| 'rolling' mirrors live get_rvol (last 10 bars) — recommended for all symbols.")


if __name__ == "__main__":
    main()
