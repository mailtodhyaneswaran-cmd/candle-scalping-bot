"""IBKR paper trading test tool — connection check and bracket order placement."""

import argparse
from ib_async import ExecutionFilter
import ibkr_connector
import config


def get_market_price(ib, contract) -> float | None:
    """Get current last-trade price via snapshot; fall back to last bar close."""
    tickers = ib.reqTickers(contract)
    if tickers and tickers[0].last and tickers[0].last > 0:
        return tickers[0].last
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="300 S",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=False,
        formatDate=1,
    )
    if bars:
        return bars[-1].close
    return None


def run_connection_test(ib, ticker: str, exchange: str, currency: str) -> None:
    contract = ibkr_connector.get_contract(ticker, exchange, currency)
    ib.qualifyContracts(contract)
    print(f"Contract qualified: {contract}")

    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="600 S",
        barSizeSetting="5 mins",
        whatToShow="TRADES",
        useRTH=False,
        formatDate=1,
    )
    if bars:
        last = bars[-1]
        print(f"Last 5-min bar — date: {last.date}  O:{last.open} H:{last.high} L:{last.low} C:{last.close} V:{last.volume}")
    else:
        print("No bars returned (market may be closed).")


def run_bracket_order(ib, ticker: str, exchange: str, currency: str,
                      action: str, qty: int, risk: float, offset: float,
                      use_market: bool = False) -> None:
    contract = ibkr_connector.get_contract(ticker, exchange, currency)
    ib.qualifyContracts(contract)
    print(f"Contract qualified: {contract}")

    ib.reqMarketDataType(3)  # delayed data — free, no subscription needed
    price = get_market_price(ib, contract)
    if price is None:
        print("Could not get market price — market may be closed.")
        return

    # For market orders use current price as the reference for TP/SL;
    # for limit orders offset the entry so it stays open during testing.
    if action == "BUY":
        entry       = round(price - offset, 2)
        ref         = price if use_market else entry
        stop_loss   = round(ref - risk, 2)
        take_profit = round(ref + config.RISK_REWARD_RATIO * risk, 2)
    else:
        entry       = round(price + offset, 2)
        ref         = price if use_market else entry
        stop_loss   = round(ref + risk, 2)
        take_profit = round(ref - config.RISK_REWARD_RATIO * risk, 2)

    rr = config.RISK_REWARD_RATIO
    entry_label = "MARKET" if use_market else f"{entry:.2f} {currency}  (offset {offset:.2f} below market)"
    print(f"\nBracket order preview — {action} {qty} x {ticker}")
    print(f"  Current price: {price:.2f} {currency}")
    print(f"  Entry:         {entry_label}")
    print(f"  Stop Loss:     {stop_loss:.2f} {currency}  (risk {risk:.2f}/share = {risk * qty:.2f} total)")
    print(f"  Take Profit:   {take_profit:.2f} {currency}  ({rr}R = {rr * risk:.2f}/share = {rr * risk * qty:.2f} total)")

    confirm = input("\nType 'yes' to submit, anything else to cancel: ").strip().lower()
    if confirm != "yes":
        print("Order cancelled.")
        return

    if use_market:
        trades = ibkr_connector.place_market_bracket_order(
            ib, contract, action, qty, take_profit, stop_loss
        )
    else:
        trades = ibkr_connector.place_bracket_order(
            ib, contract, action, qty, entry, take_profit, stop_loss
        )
    print(f"\nBracket order submitted — {len(trades)} legs:")
    for t in trades:
        print(f"  orderId={t.order.orderId}  action={t.order.action}  "
              f"type={t.order.orderType}  qty={t.order.totalQuantity}")

    # Verify within the same connection — reconnecting with the same clientId cancels orders
    print("\nVerifying orders (same session)...")
    ib.sleep(2)
    run_status(ib)


def run_status(ib) -> None:
    """Show open orders, positions, and recent fills."""
    orders = ib.reqAllOpenOrders()
    ib.sleep(2)
    if orders:
        print(f"\nOpen orders ({len(orders)}):")
        for o in orders:
            print(f"  orderId={o.order.orderId:<6} {o.order.action:<4} {o.order.totalQuantity} {o.contract.symbol}"
                  f"  type={o.order.orderType:<10} lmtPrice={getattr(o.order, 'lmtPrice', '-')}"
                  f"  auxPrice={getattr(o.order, 'auxPrice', '-')}"
                  f"  status={o.orderStatus.status}")
    else:
        print("\nNo open orders.")

    positions = ib.positions()
    if positions:
        print(f"\nPositions ({len(positions)}):")
        for p in positions:
            print(f"  {p.contract.symbol:<8} qty={p.position}  avgCost={p.avgCost:.2f}  account={p.account}")
    else:
        print("No open positions.")

    fills = ib.reqExecutions(ExecutionFilter())
    ib.sleep(1)
    if fills:
        print(f"\nRecent fills ({len(fills)}):")
        for f in fills:
            print(f"  {f.time}  {f.execution.side:<5} {f.execution.shares} {f.contract.symbol}"
                  f"  @ {f.execution.price:.2f}  orderId={f.execution.orderId}")
    else:
        print("No fills found.")


def main():
    parser = argparse.ArgumentParser(description="IBKR paper trading test tool")
    parser.add_argument("--ticker",   default=config.SYMBOL,
                        help="Ticker symbol (default: %(default)s)")
    parser.add_argument("--exchange", default="SMART",
                        help="Exchange, e.g. SMART, AEB, XETRA (default: SMART)")
    parser.add_argument("--currency", default="USD",
                        help="Currency, e.g. USD, EUR, GBP (default: USD)")
    parser.add_argument("--buy",  type=int, metavar="QTY",
                        help="Place a BUY bracket order for QTY shares")
    parser.add_argument("--sell", type=int, metavar="QTY",
                        help="Place a SELL bracket order for QTY shares")
    parser.add_argument("--risk", type=float, default=0.50, metavar="AMOUNT",
                        help="Stop distance per share in the stock's currency (default: 0.50)")
    parser.add_argument("--offset", type=float, default=5.00, metavar="AMOUNT",
                        help="Place entry this far below (buy) or above (sell) current price "
                             "so the order stays open for testing (default: 5.00)")
    parser.add_argument("--market", action="store_true",
                        help="Use a market order for entry (bypasses price %% constraint)")
    parser.add_argument("--status", action="store_true",
                        help="Show all open orders, positions, and fills")
    args = parser.parse_args()

    if args.buy and args.sell:
        print("Error: specify either --buy or --sell, not both.")
        return

    print(f"Connecting to {config.IBKR_HOST}:{config.IBKR_PORT} (clientId={config.IBKR_CLIENT_ID}) ...")
    ib = ibkr_connector.connect()
    print("Connected:", ib.isConnected())
    print("Managed accounts:", ib.managedAccounts())

    if args.status:
        run_status(ib)
    elif args.buy:
        run_bracket_order(ib, args.ticker, args.exchange, args.currency,
                          "BUY", args.buy, args.risk, args.offset, args.market)
    elif args.sell:
        run_bracket_order(ib, args.ticker, args.exchange, args.currency,
                          "SELL", args.sell, args.risk, args.offset, args.market)
    else:
        run_connection_test(ib, args.ticker, args.exchange, args.currency)

    ib.disconnect()
    print("\nDisconnected cleanly.")


if __name__ == "__main__":
    main()
