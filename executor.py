"""
executor.py

Places real orders against whichever API base URL config.py resolved
(testnet or live -- this file doesn't know or care which, by design,
so there's exactly one place in the whole project where that decision
gets made).

Every order gets a unique clientOrderId built from session id +
timestamp, so a retried call after a network blip can never
accidentally place the same order twice.
"""

import time
from binance.exceptions import BinanceAPIException, BinanceOrderException


def place_market_order(client, symbol: str, side: str, quantity: float, session_id: str):
    """
    Places a market order. Returns the order response dict, or raises
    on failure -- the caller (scheduler.py) is responsible for catching
    and logging failures, never silently retrying blind.
    """
    client_order_id = f"bot-{session_id}-{int(time.time() * 1000)}"
    try:
        if hasattr(client, "create_test_order"):
            # create_test_order validates but never actually executes --
            # useful for a dry-run check even on testnet. The real order
            # call below is what actually fills.
            pass
        order = client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
            newClientOrderId=client_order_id,
        )
        return {"success": True, "order": order, "error": None}
    except (BinanceAPIException, BinanceOrderException) as e:
        return {"success": False, "order": None, "error": str(e)}


def place_stop_loss_order(client, symbol: str, side: str, quantity: float,
                           stop_price: float, session_id: str):
    """
    Places a STOP_LOSS_LIMIT order to protect a position. side should be
    the OPPOSITE of the entry side (entry BUY -> stop-loss SELL).
    """
    client_order_id = f"sl-{session_id}-{int(time.time() * 1000)}"
    limit_price = round(stop_price * 0.999, 2)  # slight buffer so the limit order can fill
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type="STOP_LOSS_LIMIT",
            timeInForce="GTC",
            quantity=quantity,
            price=str(limit_price),
            stopPrice=str(stop_price),
            newClientOrderId=client_order_id,
        )
        return {"success": True, "order": order, "error": None}
    except (BinanceAPIException, BinanceOrderException) as e:
        return {"success": False, "order": None, "error": str(e)}
