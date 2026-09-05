"""
market_data.py

The bot's "catalog": fetches raw candles from Binance and reshapes them
into a clean, predictable format. Nothing in here decides anything --
it only fetches and formats. Keeping it dumb on purpose means the
strategy engine can be tested without a network call at all.
"""

from binance.client import Client


def get_client(api_key: str, api_secret: str, testnet: bool):
    """
    Single place that constructs the Binance client, so the
    testnet-vs-live decision is made in exactly one spot in the codebase.
    """
    client = Client(api_key, api_secret, testnet=testnet)
    return client


def get_candles(client: Client, symbol: str, interval: str, limit: int = 100):
    """
    Returns a list of dicts, oldest first:
    { open_time, open, high, low, close, volume, close_time }

    Wraps python-binance's raw kline list-of-lists format (which nobody
    can read at a glance) into named fields.
    """
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    candles = []
    for k in raw:
        candles.append(
            {
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
            }
        )
    return candles


def get_historical_candles(client: Client, symbol: str, interval: str, start_str: str, end_str: str = None):
    """
    For backtesting: pulls historical klines over a date range.
    start_str / end_str use formats python-binance understands,
    e.g. "1 Jan, 2025" or "2025-01-01".
    """
    raw = client.get_historical_klines(symbol, interval, start_str, end_str)
    candles = []
    for k in raw:
        candles.append(
            {
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
            }
        )
    return candles


def get_current_price(client: Client, symbol: str) -> float:
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])
