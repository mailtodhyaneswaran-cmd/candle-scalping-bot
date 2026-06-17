"""
Pure opening-range-breakout decision logic.
Shared by strategy.py (live) and backtest.py (historical simulation).
No IBKR, no Telegram, no wall clock — just bars in, result out.

RVOL baseline modes (set via Params.rvol_mode):
  "rolling"  — volume of the breakout bar vs the median of the N bars
                immediately before it (mirrors what get_rvol does live).
                Correct for SPY, PLTR, ASML — any symbol with a noisy open.
  "opening"  — volume vs the opening-range bars (original behaviour).
                Only appropriate when the opening bars are quiet, e.g. some
                EU ETFs at 09:00. Not recommended for US open symbols.
  "disabled" — RVOL gate is skipped entirely. Use only for diagnosis or
                symbols where you have no reliable volume baseline.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Sequence, List


@dataclass
class Bar:
    t: str           # "HH:MM" timestamp — ordering only
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Params:
    rr_ratio: float           = 2.0
    rvol_min: float           = 1.5
    rvol_mode: str            = "rolling"   # "rolling" | "opening" | "disabled"
    rvol_rolling_window: int  = 10          # bars to look back for rolling baseline
    retest_tolerance_pct: float = 0.0005    # 5 bps of price — was absolute 0.05
    min_range_pct: float      = 0.0015      # skip days where range < 0.15% of price
    slippage_pct: float       = 0.0002      # modeled fill slippage per side
    commission_per_share: float = 0.005     # set to your real IBKR schedule


@dataclass
class TradeResult:
    direction: str
    entry: float
    stop: float
    target: float
    qty: int
    exit_price: float
    exit_reason: str        # "tp" | "sl" | "range_reentry" | "session_end"
    risk_per_share: float
    r_multiple: float
    gross_pnl: float
    commission: float
    net_pnl: float
    # diagnostic fields — always populated, useful for debugging
    breakout_bar_idx: int   = 0
    retest_bar_idx: int     = 0
    rvol_at_breakout: float = 0.0


def position_size(capital: float, price: float) -> int:
    """Largest whole-share qty that fits within capital cap.
    Returns 0 when even 1 share exceeds capital (caller must handle)."""
    if price <= 0:
        return 0
    return int(capital // price)


def _rvol(bar_idx: int, bars: Sequence[Bar], opening_bars: Sequence[Bar],
          params: Params) -> float:
    """Compute RVOL for bars[bar_idx] using the mode set in params."""
    if params.rvol_mode == "disabled":
        return params.rvol_min          # always passes the gate

    if params.rvol_mode == "opening":
        vols = [b.volume for b in opening_bars if b.volume > 0]
        if not vols:
            return 1.0
        import statistics
        base = statistics.median(vols)
        return bars[bar_idx].volume / base if base > 0 else 1.0

    # "rolling" — median of the N bars immediately before this one,
    # within the post-opening slice.  Mirrors live get_rvol behaviour.
    import statistics
    start = max(0, bar_idx - params.rvol_rolling_window)
    lookback = [b.volume for b in bars[start:bar_idx] if b.volume > 0]
    if not lookback:
        return 1.0
    base = statistics.median(lookback)
    return bars[bar_idx].volume / base if base > 0 else 1.0


def simulate_session(
    opening_bars: Sequence[Bar],   # the 5 bars that define the range (09:00–09:04)
    post_bars: Sequence[Bar],      # bars after the range closes (09:05 onward)
    capital: float,
    params: Params,
) -> Optional[TradeResult]:
    """Replay one session and return the single trade taken, or None.

    Signature change vs v1: opening_bars and post_bars are passed separately
    so the RVOL baseline always has access to the opening bars regardless of
    how the caller slices the data.
    """
    if not post_bars:
        return None

    oh = max(b.high for b in opening_bars)
    ol = min(b.low for b in opening_bars)
    price_ref = (oh + ol) / 2.0

    # ── Range floor: skip days too thin for 2R to clear costs ────────────
    if (oh - ol) < params.min_range_pct * price_ref:
        return None

    tol = params.retest_tolerance_pct * price_ref
    direction: Optional[str] = None
    breakout_level: Optional[float] = None
    breakout_idx: int = 0
    rvol_at_break: float = 0.0

    i, n = 0, len(post_bars)
    while i < n:
        bar = post_bars[i]

        # ── Breakout detection ────────────────────────────────────────────
        if direction is None:
            broke_up = bar.close > oh
            broke_dn = bar.close < ol
            if broke_up or broke_dn:
                rv = _rvol(i, post_bars, opening_bars, params)
                if rv >= params.rvol_min:
                    direction = "long" if broke_up else "short"
                    breakout_level = oh if broke_up else ol
                    breakout_idx = i
                    rvol_at_break = rv
            i += 1
            continue

        # ── Retest detection ──────────────────────────────────────────────
        if direction == "long":
            touched  = bar.low  <= breakout_level + tol
            rejection = bar.close > breakout_level
            failure  = bar.close < ol
        else:
            touched  = bar.high >= breakout_level - tol
            rejection = bar.close < breakout_level
            failure  = bar.close > oh

        if touched and failure:
            return None     # failed retest — stand down for the day

        if touched and rejection:
            return _enter_and_manage(
                post_bars, i + 1,
                direction, breakout_level, oh, ol,
                capital, params,
                breakout_idx, i, rvol_at_break,
            )
        i += 1

    return None


def _enter_and_manage(
    bars: Sequence[Bar], start: int,
    direction: str, breakout_level: float,
    opening_high: float, opening_low: float,
    capital: float, params: Params,
    breakout_idx: int, retest_idx: int, rvol_at_break: float,
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
        return None
    qty = position_size(capital, entry)
    if qty == 0:
        return None

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
        # conservative: a bar that hits both is counted as the stop
        if hit_sl:
            exit_price, reason = stop,   "sl";            break
        if hit_tp:
            exit_price, reason = target, "tp";            break
        if reentered:
            exit_price, reason = bar.close, "range_reentry"; break
    else:
        exit_price = bars[start - 1].close if start > 0 else entry
        reason     = "session_end"

    gross      = (exit_price - entry) * qty if direction == "long" else (entry - exit_price) * qty
    commission = params.commission_per_share * qty * 2
    net        = gross - commission
    r_multiple = gross / (risk * qty) if risk * qty else 0.0

    return TradeResult(
        direction, entry, stop, target, qty,
        exit_price, reason,
        risk, r_multiple, gross, commission, net,
        breakout_idx, retest_idx, rvol_at_break,
    )
