"""
Pure opening-range-breakout decision logic.
Shared by strategy.py (live) and backtest.py (historical simulation).
No IBKR, no Telegram, no wall clock — just bars in, result out.

RVOL baseline modes (set via Params.rvol_mode):
  "rolling"  — volume of the breakout bar vs the median of the N bars
                immediately before it in the full bar list (including any
                pre-window warmup bars).  Matches live get_rvol exactly.
                Default and recommended for all symbols including SPY.
  "opening"  — volume vs the opening-range bars.
                Only appropriate when the opening bars themselves are quiet.
  "disabled" — RVOL gate skipped entirely. Diagnosis only.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class Bar:
    t: str           # "HH:MM" — ordering only
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Params:
    rr_ratio:               float = 2.0
    rvol_min:               float = 1.5
    rvol_mode:              str   = "rolling"   # "rolling" | "opening" | "disabled"
    rvol_rolling_window:    int   = 10
    retest_tolerance_pct:   float = 0.0005      # 5 bps of mid-range price
    min_range_pct:          float = 0.0015      # skip days where range < 0.15% of price
    slippage_pct:           float = 0.0002
    commission_per_share:   float = 0.005


@dataclass
class TradeResult:
    direction:          str
    entry:              float
    stop:               float
    target:             float
    qty:                int
    exit_price:         float
    exit_reason:        str     # "tp" | "sl" | "range_reentry" | "session_end"
    risk_per_share:     float
    r_multiple:         float
    gross_pnl:          float
    commission:         float
    net_pnl:            float
    breakout_bar_idx:   int   = 0
    retest_bar_idx:     int   = 0
    rvol_at_breakout:   float = 0.0


def position_size(capital: float, price: float) -> int:
    """Largest whole-share qty within capital cap. Returns 0 if 1 share > cap."""
    if price <= 0:
        return 0
    return int(capital // price)


def _rvol(
    global_idx: int,          # index of the bar in `all_bars`
    all_bars: Sequence[Bar],  # full list: warmup + opening + post
    opening_bars: Sequence[Bar],
    params: Params,
) -> float:
    """Compute RVOL for all_bars[global_idx].

    Rolling mode uses the N bars immediately before global_idx in all_bars —
    this includes pre-window warmup bars, exactly matching live get_rvol which
    fetches 30 min of history ending at the current bar.
    """
    import statistics

    if params.rvol_mode == "disabled":
        return params.rvol_min          # always passes

    if params.rvol_mode == "opening":
        vols = [b.volume for b in opening_bars if b.volume > 0]
        if not vols:
            return 1.0
        base = statistics.median(vols)
        return all_bars[global_idx].volume / base if base > 0 else 1.0

    # "rolling": median of the N bars before this one in the full list
    start = max(0, global_idx - params.rvol_rolling_window)
    lookback = [b.volume for b in all_bars[start:global_idx] if b.volume > 0]
    if not lookback:
        return 1.0
    base = statistics.median(lookback)
    return all_bars[global_idx].volume / base if base > 0 else 1.0


def simulate_session(
    opening_bars: Sequence[Bar],
    post_bars: Sequence[Bar],
    capital: float,
    params: Params,
    warmup_bars: Sequence[Bar] = (),
    verbose: bool = False,
) -> Optional[TradeResult]:
    """Replay one session and return the single trade taken, or None."""
    if not post_bars:
        return None

    oh = max(b.high for b in opening_bars)
    ol = min(b.low  for b in opening_bars)
    price_ref = (oh + ol) / 2.0
    op_range  = oh - ol
    tol       = params.retest_tolerance_pct * price_ref

    if verbose:
        print(f"      Range: {ol:.2f}–{oh:.2f}  spread={op_range:.3f}"
              f"  floor={params.min_range_pct * price_ref:.3f}"
              f"  tol={tol:.3f}")

    if op_range < params.min_range_pct * price_ref:
        if verbose:
            print(f"      → SKIP: range too thin ({op_range:.3f} < {params.min_range_pct * price_ref:.3f})")
        return None

    all_bars    = list(warmup_bars) + list(opening_bars) + list(post_bars)
    post_offset = len(warmup_bars) + len(opening_bars)

    direction:      Optional[str]   = None
    breakout_level: Optional[float] = None
    breakout_idx:   int             = 0
    rvol_at_break:  float           = 0.0

    i, n = 0, len(post_bars)
    while i < n:
        bar        = post_bars[i]
        global_idx = post_offset + i

        if direction is None:
            broke_up = bar.close > oh
            broke_dn = bar.close < ol

            if verbose:
                arrow = (f"  ↑ broke above OH {oh:.2f}" if broke_up else
                         f"  ↓ broke below OL {ol:.2f}" if broke_dn else "")
                print(f"      [{bar.t}] O:{bar.open:.2f} H:{bar.high:.2f} "
                      f"L:{bar.low:.2f} C:{bar.close:.2f} V:{bar.volume:.0f}"
                      f"  | breakout?{arrow}")

            if broke_up or broke_dn:
                rv = _rvol(global_idx, all_bars, opening_bars, params)
                if rv >= params.rvol_min:
                    direction      = "long" if broke_up else "short"
                    breakout_level = oh if broke_up else ol
                    breakout_idx   = i
                    rvol_at_break  = rv
                    if verbose:
                        print(f"      → BREAKOUT {direction.upper()} at {breakout_level:.2f}"
                              f"  RVOL={rv:.2f}x ✓")
                elif verbose:
                    print(f"      → breakout ignored — RVOL={rv:.2f}x < {params.rvol_min}x")
            i += 1
            continue

        # Retest detection
        if direction == "long":
            touched   = bar.low   <= breakout_level + tol
            rejection = bar.close >  breakout_level
            failure   = bar.close <  ol
        else:
            touched   = bar.high  >= breakout_level - tol
            rejection = bar.close <  breakout_level
            failure   = bar.close >  oh

        if verbose:
            flags = []
            if touched:   flags.append("touched")
            if rejection: flags.append("rejection✓")
            if failure:   flags.append("FAILED✗")
            print(f"      [{bar.t}] O:{bar.open:.2f} H:{bar.high:.2f} "
                  f"L:{bar.low:.2f} C:{bar.close:.2f} V:{bar.volume:.0f}"
                  f"  | retest  {' '.join(flags) if flags else '—'}")

        if touched and failure:
            if verbose:
                print(f"      → FAILED RETEST — close {bar.close:.2f} back inside range. Skip day.")
            return None

        if touched and rejection:
            if verbose:
                print(f"      → RETEST OK at {bar.t} — entering trade...")
            return _enter_and_manage(
                post_bars, i + 1,
                direction, breakout_level, oh, ol,
                capital, params,
                breakout_idx, i, rvol_at_break,
                verbose=verbose,
            )
        i += 1

    if verbose:
        print(f"      → Session ended — no confirmed retest.")
    return None


def _enter_and_manage(
    bars: Sequence[Bar], start: int,
    direction: str, breakout_level: float,
    opening_high: float, opening_low: float,
    capital: float, params: Params,
    breakout_idx: int, retest_idx: int, rvol_at_break: float,
    verbose: bool = False,
) -> Optional[TradeResult]:
    slip = params.slippage_pct * breakout_level
    if direction == "long":
        entry  = breakout_level + slip
        stop   = opening_low
        risk   = entry - stop
        target = entry + params.rr_ratio * risk
    else:
        entry  = breakout_level - slip
        stop   = opening_high
        risk   = stop - entry
        target = entry - params.rr_ratio * risk

    if risk <= 0:
        if verbose: print(f"      → SKIP: risk <= 0")
        return None
    qty = position_size(capital, entry)
    if qty == 0:
        if verbose: print(f"      → SKIP: entry {entry:.2f} exceeds capital")
        return None

    if verbose:
        print(f"      ORDER: {direction.upper()}  entry={entry:.2f}  "
              f"stop={stop:.2f}  target={target:.2f}  qty={qty}  risk/share={risk:.3f}")

    exit_price, reason = entry, "session_end"
    for bar in bars[start:]:
        if direction == "long":
            hit_sl    = bar.low   <= stop
            hit_tp    = bar.high  >= target
            reentered = bar.close <  breakout_level
        else:
            hit_sl    = bar.high  >= stop
            hit_tp    = bar.low   <= target
            reentered = bar.close >  breakout_level

        if verbose:
            flags = []
            if hit_tp:    flags.append("TP!")
            if hit_sl:    flags.append("SL!")
            if reentered: flags.append("RE-ENTRY!")
            print(f"      [{bar.t}] O:{bar.open:.2f} H:{bar.high:.2f} "
                  f"L:{bar.low:.2f} C:{bar.close:.2f} V:{bar.volume:.0f}"
                  f"  | managing  {'  '.join(flags) if flags else '...'}")

        if hit_sl:
            exit_price, reason = stop,      "sl"
            if verbose: print(f"      → SL HIT at {stop:.2f}")
            break
        if hit_tp:
            exit_price, reason = target,    "tp"
            if verbose: print(f"      → TP HIT at {target:.2f}")
            break
        if reentered:
            exit_price, reason = bar.close, "range_reentry"
            if verbose: print(f"      → RANGE RE-ENTRY — exit at {bar.close:.2f}")
            break
    else:
        exit_price = bars[start - 1].close if start > 0 else entry
        reason     = "session_end"
        if verbose: print(f"      → SESSION END — exit at {exit_price:.2f}")

    gross      = (exit_price - entry) * qty if direction == "long" else (entry - exit_price) * qty
    commission = params.commission_per_share * qty * 2
    net        = gross - commission
    r_multiple = gross / (risk * qty) if risk * qty else 0.0

    if verbose:
        icon = "✅" if net > 0 else "❌"
        print(f"      RESULT: gross={gross:+.2f}  commission={commission:.2f}"
              f"  net={net:+.2f}  R={r_multiple:.2f}  {icon}")

    return TradeResult(
        direction, entry, stop, target, qty,
        exit_price, reason,
        risk, r_multiple, gross, commission, net,
        breakout_idx, retest_idx, rvol_at_break,
    )