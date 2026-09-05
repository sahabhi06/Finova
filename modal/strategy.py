"""
strategy.py

The bot's "brain" -- turns a list of candles into a signal. Pure
functions only: no API calls, no side effects. That means you can test
this against hand-computed examples with zero network access, which is
exactly what test_strategy.py does.

Returns a signal, never an order. The risk engine sits between this and
any actual money movement.
"""

from typing import List, Dict


def sma(candles: List[Dict], window: int) -> List[float]:
    """
    Simple Moving Average. Returns a list the same length as candles,
    with None for indices before there's enough data for a full window.
    """
    closes = [c["close"] for c in candles]
    result = [None] * len(closes)
    for i in range(window - 1, len(closes)):
        window_slice = closes[i - window + 1 : i + 1]
        result[i] = sum(window_slice) / window
    return result


def rsi(candles: List[Dict], window: int = 14) -> List[float]:
    """
    Relative Strength Index. Standard Wilder smoothing.
    Returns a list the same length as candles, None where undefined.
    """
    closes = [c["close"] for c in candles]
    result = [None] * len(closes)
    if len(closes) <= window:
        return result

    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window

    def rsi_from_avgs(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    result[window] = rsi_from_avgs(avg_gain, avg_loss)

    for i in range(window, len(gains)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        result[i + 1] = rsi_from_avgs(avg_gain, avg_loss)

    return result


def atr(candles: List[Dict], window: int = 14) -> List[float]:
    """
    Average True Range — measures volatility as the smoothed average of
    the true range over `window` periods.  True range accounts for gaps
    by comparing the current high/low against the previous close.

    Returns a list the same length as candles, None where undefined.
    """
    result = [None] * len(candles)
    if len(candles) < window + 1:
        return result

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    # First ATR is the simple average of the first `window` true ranges
    first_atr = sum(true_ranges[:window]) / window
    result[window] = first_atr

    # Subsequent values use Wilder smoothing (same as RSI)
    current_atr = first_atr
    for i in range(window, len(true_ranges)):
        current_atr = (current_atr * (window - 1) + true_ranges[i]) / window
        result[i + 1] = current_atr

    return result


def compute_atr_stop_distance(candles: List[Dict], atr_window: int = 14,
                               atr_multiplier: float = 2.0) -> float:
    """
    Returns the stop-loss distance in price units based on ATR.
    A multiplier of 2.0 means the stop sits 2× the current ATR away
    from the entry — wide enough to survive normal noise, tight enough
    to limit damage from genuine reversals.

    Returns None if ATR cannot be computed (not enough data).
    """
    atr_values = atr(candles, atr_window)
    current_atr = atr_values[-1]
    if current_atr is None:
        return None
    return current_atr * atr_multiplier


def generate_signal(candles: List[Dict], fast_window: int = 10, slow_window: int = 30,
                     rsi_window: int = 14, rsi_overbought: float = 70, trend_filter_window: int = None) -> Dict:
    """
    SMA(fast) crossing above SMA(slow) -> candidate BUY, unless RSI says
    the asset is already overbought, or price is below the trend filter.
    SMA(fast) crossing below SMA(slow) -> SELL (exit), no RSI filter --
    exits shouldn't be filtered, only new entries.
    """
    min_required_candles = max(slow_window, trend_filter_window if trend_filter_window else 0) + 1
    if len(candles) < min_required_candles:
        return {"action": "HOLD", "reason": f"Not enough data (need {min_required_candles} candles)"}

    fast = sma(candles, fast_window)
    slow = sma(candles, slow_window)
    rsi_values = rsi(candles, rsi_window)
    
    trend_sma = None
    if trend_filter_window:
        trend_sma = sma(candles, trend_filter_window)

    if fast[-1] is None or slow[-1] is None or fast[-2] is None or slow[-2] is None:
        return {"action": "HOLD", "reason": "Indicators not yet warmed up"}
        
    if trend_filter_window and trend_sma[-1] is None:
        return {"action": "HOLD", "reason": "Indicators not yet warmed up"}

    crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
    crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
    current_rsi = rsi_values[-1]
    current_price = candles[-1]["close"]

    if crossed_up:
        if current_rsi is not None and current_rsi >= rsi_overbought:
            return {
                "action": "HOLD",
                "reason": f"SMA crossed up but RSI={current_rsi:.1f} is overbought (>= {rsi_overbought})",
            }
        if trend_filter_window and trend_sma[-1] is not None and current_price < trend_sma[-1]:
            return {
                "action": "HOLD",
                "reason": f"SMA crossed up but price < SMA{trend_filter_window} trend filter",
            }
            
        return {
            "action": "BUY",
            "reason": f"SMA{fast_window} crossed above SMA{slow_window}, RSI={current_rsi:.1f}" if current_rsi else "SMA crossed up",
        }

    if crossed_down:
        return {
            "action": "SELL",
            "reason": f"SMA{fast_window} crossed below SMA{slow_window}",
        }

    return {"action": "HOLD", "reason": "No crossover this candle"}
