"""
Multi-day, multi-symbol backtester for the opening-range-breakout strategy.

Purpose: compare candidate symbols (VUSA vs ASML vs an index ETF, etc.) on the
EXACT same logic the live bot runs, so symbol choice is an evidence decision.

Two data sources:
  1. IBKR historical (default) — needs TWS/IB Gateway running, same as the bot.
  2. CSV  (--csv DIR)          — offline; files named SYMBOL_YYYY-MM-DD.csv with
                                 columns: time,open,high,low,close,volume
                                 (time = ISO local timestamp for the session day).

Usage:
  python backtest.py --days 60
  python backtest.py --days 60 --symbols ASML:AEB:EUR EXS1:IBIS:EUR VUSA:AEB:EUR
  python backtest.py --csv ./data --symbols ASML:AEB:EUR

The opening range = first 5 one-minute bars of the window (09:00–09:04).
Trades are simulated from 09:05 to session_end. RVOL is approximated as a bar's
volume / the median volume for that clock-minute across the sample (documented
limitation: the live get_rvol may use a different baseline).
"""
from __future__ import annotations
import argparse
import csv
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_core import Bar, Params, simulate_session

NL = ZoneInfo("Europe/Amsterdam")

# window is fixed to the EU session; pass --us to flip to the US window
EU_WINDOW = ("09:00", "09:05", "10:30")
US_WINDOW = ("15:30", "15:35", "17:00")


@dataclass
class SymbolSpec:
    symbol: str
    exchange: str
    currency: str

    @classmethod
    def parse(cls, s: str) -> "SymbolSpec":
        sym, exch, cur = s.split(":")
        return cls(sym, exch, cur)


# ───────────────────────── data sources ─────────────────────────
def _hhmm(s: str):
    h, m = map(int, s.split(":")); return h, m


def _window_bounds(day: datetime, window):
    (oh, om), (_, _), (eh, em) = (_hhmm(window[0]), _hhmm(window[1]), _hhmm(window[2]))
    start = day.replace(hour=oh, minute=om, second=0, microsecond=0)
    end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start, end


def fetch_ibkr(spec: SymbolSpec, days: int, window):
    from ib_async import IB, Stock
    print(f"  Connecting to IBKR (TWS port 7497)...")
    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=77)  # paper port; read-only is fine
    print(f"  Connected. Qualifying contract {spec.symbol} on {spec.exchange}...")
    contract = Stock(spec.symbol, spec.exchange, spec.currency)
    ib.qualifyContracts(contract)
    print(f"  Contract OK. Fetching {days} trading days of 1-min bars...")

    out = {}  # date_str -> list[Bar]
    today = datetime.now(NL)
    fetched = 0
    back = 0
    while fetched < days and back < days * 3:  # skip weekends/holidays
        day = today - timedelta(days=back)
        back += 1
        if day.weekday() >= 5:
            continue
        start, end = _window_bounds(day, window)
        date_str = day.strftime("%Y-%m-%d")
        print(f"  [{fetched+1:>3}/{days}] {date_str} ... ", end="", flush=True)
        bars = ib.reqHistoricalData(
            contract, endDateTime=end, durationStr="7200 S",
            barSizeSetting="1 min", whatToShow="TRADES",
            useRTH=False, formatDate=2,
        )
        rows = []
        for b in bars:
            t = b.date.astimezone(NL) if hasattr(b.date, "astimezone") else b.date
            if start <= t <= end:
                rows.append(Bar(t.strftime("%H:%M"), b.open, b.high, b.low, b.close, b.volume))
        if len(rows) >= 6:
            out[date_str] = rows
            fetched += 1
            print(f"{len(rows)} bars")
        else:
            print(f"skipped (only {len(rows)} bars — holiday/early close?)")
    print(f"  Done. {fetched} days fetched. Disconnecting...")
    ib.disconnect()
    return out


def load_csv(spec: SymbolSpec, directory: str):
    print(f"  Loading CSV files for {spec.symbol} from {directory}/ ...")
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
            print(f"    {fn}  ({len(rows)} bars)")
        else:
            print(f"    {fn}  skipped — too few bars")
    print(f"  {len(out)} days loaded from CSV.")
    return out


# ───────────────────────── run one symbol ─────────────────────────
def run_symbol(spec, days_bars, capital, params, no_rvol=False):
    """RVOL proxy = breakout bar volume / median volume of that day's opening-range
    bars (i.e. is volume expanding vs the pre-breakout baseline). This avoids the
    cross-day degeneracy where every day spikes at the same minute. Swap in your
    live get_rvol baseline here if you want exact parity."""
    trades = []
    rvol_label = " (RVOL gate OFF)" if no_rvol else ""
    print(f"  Simulating {len(days_bars)} days{rvol_label}...")
    for date_str, rows in sorted(days_bars.items()):
        opening = rows[:5]                       # 09:00–09:04
        post = rows[5:]                           # 09:05 onward
        if not post:
            continue
        oh = max(b.high for b in opening)
        ol = min(b.low for b in opening)
        base_vol = statistics.median([b.volume for b in opening]) or 1.0

        rvol_of = None if no_rvol else (lambda _, bar, _bv=base_vol: bar.volume / _bv)

        tr = simulate_session(post, oh, ol, capital, params, rvol_of=rvol_of)
        if tr:
            trades.append((date_str, tr))
            icon = "✅" if tr.net_pnl > 0 else "❌"
            print(f"    {date_str}  range {ol:.2f}–{oh:.2f}  "
                  f"{tr.direction:5}  entry {tr.entry:.2f}  "
                  f"exit {tr.exit_price:.2f} ({tr.exit_reason:12})  "
                  f"net {tr.net_pnl:+.2f}  {icon}")
        else:
            print(f"    {date_str}  range {ol:.2f}–{oh:.2f}  no setup")
    return trades


def summarize(spec, trades, n_days):
    if not trades:
        return dict(symbol=spec.symbol, days=n_days, trades=0)
    rs = [t.r_multiple for _, t in trades]
    nets = [t.net_pnl for _, t in trades]
    wins = [t for _, t in trades if t.net_pnl > 0]
    gross_win = sum(t.net_pnl for _, t in trades if t.net_pnl > 0)
    gross_loss = -sum(t.net_pnl for _, t in trades if t.net_pnl < 0)
    pf = (gross_win / gross_loss) if gross_loss else float("inf")
    return dict(
        symbol=spec.symbol, days=n_days, trades=len(trades),
        win_pct=100 * len(wins) / len(trades),
        avg_R=statistics.mean(rs), total_R=sum(rs),
        net=sum(nets), profit_factor=pf,
        tp=sum(1 for _, t in trades if t.exit_reason == "tp"),
        sl=sum(1 for _, t in trades if t.exit_reason == "sl"),
        reentry=sum(1 for _, t in trades if t.exit_reason == "range_reentry"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--capital", type=float, default=500)
    ap.add_argument("--csv", default=None, help="directory of SYMBOL_DATE.csv files")
    ap.add_argument("--no-rvol", action="store_true", help="bypass RVOL gate (diagnosis only)")
    ap.add_argument("--us", action="store_true", help="use the US window instead of EU")
    ap.add_argument("--symbols", nargs="+",
                    default=["VUSA:AEB:EUR", "ASML:AEB:EUR", "EXS1:IBIS:EUR"])
    args = ap.parse_args()
    window = US_WINDOW if args.us else EU_WINDOW
    params = Params()

    rows = []
    for s in args.symbols:
        spec = SymbolSpec.parse(s)
        print(f"\n{'─'*55}\n  {spec.symbol}  ({spec.exchange} / {spec.currency})\n{'─'*55}")
        data = load_csv(spec, args.csv) if args.csv else fetch_ibkr(spec, args.days, window)
        trades = run_symbol(spec, data, args.capital, params, no_rvol=args.no_rvol)
        rows.append(summarize(spec, trades, len(data)))
        print(f"  → {len(trades)} trade(s) out of {len(data)} days")

    hdr = f"{'symbol':8} {'days':>4} {'trades':>6} {'win%':>6} {'avgR':>6} {'totR':>7} {'net':>9} {'PF':>5}  tp/sl/re"
    print("\n" + hdr); print("-" * len(hdr))
    for r in rows:
        if r["trades"] == 0:
            print(f"{r['symbol']:8} {r['days']:>4} {0:>6}      —      —       —         —      —   no setups")
            continue
        print(f"{r['symbol']:8} {r['days']:>4} {r['trades']:>6} {r['win_pct']:>5.0f}% "
              f"{r['avg_R']:>6.2f} {r['total_R']:>7.1f} {r['net']:>9.2f} {r['profit_factor']:>5.2f}  "
              f"{r['tp']}/{r['sl']}/{r['reentry']}")
    print("\nNote: RVOL here is a per-minute median approximation; tune Params to match\n"
          "your live get_rvol before trusting absolute numbers. Compare symbols relatively.")


if __name__ == "__main__":
    main()
