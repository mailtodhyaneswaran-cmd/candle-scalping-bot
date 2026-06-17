"""
Pure opening-range-breakout decision logic.

This is the piece that was previously tangled inside strategy.py's live loop.
Extracting it is what makes the strategy back-testable: bars in, a trade result
out — no IBKR, no Telegram, no wall clock. The live bot and backtest.py both
import from here so they can never drift apart.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence, Callable


@dataclass
class Bar:
    t: str            # timestamp (anything printable); only ordering matters
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Params:
    rr_ratio: float = 2.0
    rvol_min: float = 1.5
    # was an ABSOLUTE 0.05 in the original config — meaningless across price levels.
    # Now a fraction of price so it scales (0.0005 = 5 bps).
    retest_tolerance_pct: float = 0.0005
    # NEW: skip days whose opening range is too thin for 2R to clear costs.
    min_range_pct: float = 0.0015          # 0.15% of price
    slippage_pct: float = 0.0002           # modeled fill slippage per side
    commission_per_share: float = 0.005    # set to your real IBKR schedule


@dataclass
class TradeResult:
    direction: str
    entry: float
    stop: float
    target: float
    qty: int
    exit_price: float
    exit_reason: str          # tp | sl | range_reentry | session_end
    risk_per_share: float
    r_multiple: float
    gross_pnl: float
    commission: float
    net_pnl: float


def position_size(capital: float, price: float) -> int:
    """Largest whole-share position that fits the capital cap.
    Returns 0 when a single share is unaffordable (the original code forced 1,
    silently breaching the cap for expensive symbols)."""
    if price <= 0:
        return 0
    return int(capital // price)


def simulate_session(
    bars: Sequence[Bar],
    opening_high: float,
    opening_low: float,
    capital: float,
    params: Params,
    rvol_of: Optional[Callable[[int, Bar], float]] = None,
) -> Optional[TradeResult]:
    """Replay one session's 1-min bars (those AFTER the opening range is fixed)
    and return the single trade taken, or None for no clean setup / range too
    thin / unaffordable.

    rvol_of(i, bar) -> relative volume for bar i. If None, the RVOL gate passes
    (only use that when you genuinely have no volume baseline)."""
    price_ref = (opening_high + opening_low) / 2.0
    op_range = opening_high - opening_low

    # Guard: ignore days where the range is too thin for the target to beat costs.
    if op_range < params.min_range_pct * price_ref:
        return None

    tol = params.retest_tolerance_pct * price_ref
    direction: Optional[str] = None
    breakout_level: Optional[float] = None

    i, n = 0, len(bars)
    while i < n:
        bar = bars[i]

        if direction is None:
            broke_up = bar.close > opening_high
            broke_dn = bar.close < opening_low
            if broke_up or broke_dn:
                rvol = rvol_of(i, bar) if rvol_of else params.rvol_min
                if rvol >= params.rvol_min:
                    direction = "long" if broke_up else "short"
                    breakout_level = opening_high if broke_up else opening_low
            i += 1
            continue

        # watching for retest
        if direction == "long":
            touched = bar.low <= breakout_level + tol
            rejection = bar.close > breakout_level
            failure = bar.close < opening_low
        else:
            touched = bar.high >= breakout_level - tol
            rejection = bar.close < breakout_level
            failure = bar.close > opening_high

        if touched and failure:
            return None  # failed retest -> stand down for the day
        if touched and rejection:
            return _enter_and_manage(
                bars, i + 1, direction, breakout_level,
                opening_high, opening_low, capital, params,
            )
        i += 1

    return None


def _enter_and_manage(bars, start, direction, breakout_level,
                      opening_high, opening_low, capital, params) -> Optional[TradeResult]:
    slip = params.slippage_pct * breakout_level
    if direction == "long":
        entry = breakout_level + slip
        stop = opening_low
        risk = entry - stop
        target = entry + params.rr_ratio * risk
    else:
        entry = breakout_level - slip
        stop = opening_high
        risk = stop - entry
        target = entry - params.rr_ratio * risk

    if risk <= 0:
        return None
    qty = position_size(capital, entry)
    if qty == 0:
        return None

    exit_price, reason = entry, "session_end"
    managed = bars[start:]
    for bar in managed:
        if direction == "long":
            hit_sl = bar.low <= stop
            hit_tp = bar.high >= target
            reentered = bar.close < breakout_level
        else:
            hit_sl = bar.high >= stop
            hit_tp = bar.low <= target
            reentered = bar.close > breakout_level
        # conservative: a bar that spans both is counted as the stop
        if hit_sl:
            exit_price, reason = stop, "sl"; break
        if hit_tp:
            exit_price, reason = target, "tp"; break
        if reentered:
            exit_price, reason = bar.close, "range_reentry"; break
    else:
        exit_price = managed[-1].close if managed else entry
        reason = "session_end"

    gross = (exit_price - entry) * qty if direction == "long" else (entry - exit_price) * qty
    commission = params.commission_per_share * qty * 2
    net = gross - commission
    r_multiple = gross / (risk * qty) if risk * qty else 0.0
    return TradeResult(direction, entry, stop, target, qty, exit_price, reason,
                       risk, r_multiple, gross, commission, net)
