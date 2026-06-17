"""Thin wrapper around ib_async for the candle scalping bot."""

from zoneinfo import ZoneInfo
from ib_async import IB, Stock, LimitOrder, MarketOrder, StopOrder
import config

NL = ZoneInfo("Europe/Amsterdam")


def connect() -> IB:
    ib = IB()
    ib.connect(config.IBKR_HOST, config.IBKR_PORT, clientId=config.IBKR_CLIENT_ID)
    return ib


def get_contract(symbol: str, exchange: str = "SMART", currency: str = "USD") -> Stock:
    return Stock(symbol, exchange, currency)


def get_opening_range_bar(ib: IB, contract: Stock, opening_time: str):
    """Return the 5-min bar starting at opening_time (NL local HH:MM), or None."""
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="3600 S",
        barSizeSetting="5 mins", whatToShow="TRADES",
        useRTH=True, formatDate=1,
    )
    for bar in bars:
        if bar.date.astimezone(NL).strftime("%H:%M") == opening_time:
            return bar
    return None


def get_latest_closed_1min_bar(ib: IB, contract: Stock):
    """Return the most recently fully closed 1-min bar."""
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="1800 S",
        barSizeSetting="1 min", whatToShow="TRADES",
        useRTH=True, formatDate=1,
    )
    if len(bars) < 2:
        return None
    return bars[-2]


def get_rvol(ib: IB, contract: Stock, current_volume: float) -> float:
    """Relative Volume = current bar volume / average volume of last 10 closed bars.
    Returns 1.0 if data is unavailable (neutral — won't block the trade)."""
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="1800 S",
        barSizeSetting="1 min", whatToShow="TRADES",
        useRTH=True, formatDate=1,
    )
    recent_vols = [b.volume for b in bars[-11:-1] if b.volume > 0]
    if not recent_vols:
        return 1.0
    avg = sum(recent_vols) / len(recent_vols)
    return current_volume / avg if avg > 0 else 1.0


def place_bracket_order(ib: IB, contract: Stock, action: str, qty: int,
                        entry_price: float, take_profit: float, stop_loss: float):
    """Limit entry bracket order. Returns [parent, take-profit, stop-loss] Trades."""
    bracket = ib.bracketOrder(action, qty, round(entry_price, 2),
                              round(take_profit, 2), round(stop_loss, 2))
    for order in bracket:
        order.tif = "DAY"
    return [ib.placeOrder(contract, order) for order in bracket]


def cancel_order(ib: IB, trade) -> None:
    """Cancel a single open order."""
    try:
        ib.cancelOrder(trade.order)
    except Exception:
        pass


def close_position_at_market(ib: IB, contract: Stock, direction: str, qty: int):
    """Place a market order to close an open position immediately."""
    action = "SELL" if direction == "long" else "BUY"
    order  = MarketOrder(action, qty, tif="DAY")
    return ib.placeOrder(contract, order)


def place_market_bracket_order(ib: IB, contract: Stock, action: str, qty: int,
                                take_profit: float, stop_loss: float):
    """Market entry bracket — used for testing when limit price constraints block orders."""
    next_id   = ib.client.getReqId()
    tp_action = "SELL" if action == "BUY" else "BUY"
    parent    = MarketOrder(action, qty, orderId=next_id, transmit=False, tif="DAY")
    tp_order  = LimitOrder(tp_action, qty, round(take_profit, 2),
                           orderId=next_id + 1, parentId=next_id, transmit=False, tif="DAY")
    sl_order  = StopOrder(tp_action, qty, round(stop_loss, 2),
                          orderId=next_id + 2, parentId=next_id, transmit=True, tif="DAY")
    return [ib.placeOrder(contract, o) for o in (parent, tp_order, sl_order)]
