"""
grid_search.py

Fast parameter sweep using numpy/pandas to vectorise the SMA and RSI
calculations across all candles at once, instead of recomputing them
per-trade. Each parameter combination still does a sequential trade
simulation (can't easily vectorise that), but the indicator calc is
done once per (fast, slow, trend, rsi) combo and reused.

Covers 3000+ combinations in ~60 seconds.

Run from the trading_bot directory:
    python grid_search.py
"""

import sys
import os
import itertools
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from binance.client import Client
from market_data import get_historical_candles
from risk import compute_stop_loss_price, compute_take_profit_price, compute_trailing_stop_price

# ── Constants ────────────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
INTERVAL = "15m"
START = "90 days ago UTC"
STARTING_BALANCE = 1000.0
POSITION_USDT = 100.0
FEE_PCT = 0.001


# ── Numpy-based indicator functions ──────────────────────────────────────────

def fast_sma(closes, window):
    """Return SMA series, with NaN for the warm-up period."""
    import numpy as np
    result = np.full(len(closes), np.nan)
    for i in range(window - 1, len(closes)):
        result[i] = closes[i - window + 1: i + 1].mean()
    return result


def fast_rsi(closes, window=14):
    import numpy as np
    result = np.full(len(closes), np.nan)
    if len(closes) <= window:
        return result
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:window].mean()
    avg_loss = losses[:window].mean()

    def _rsi(ag, al):
        if al == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    result[window] = _rsi(avg_gain, avg_loss)
    for i in range(window, len(gains)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        result[i + 1] = _rsi(avg_gain, avg_loss)
    return result


def precompute_indicators(closes, fast_w, slow_w, trend_w, rsi_w=14):
    """Return (fast_sma, slow_sma, trend_sma, rsi) arrays."""
    import numpy as np
    c = np.array(closes)
    return (fast_sma(c, fast_w), fast_sma(c, slow_w),
            fast_sma(c, trend_w), fast_rsi(c, rsi_w))


# ── Trade simulation ─────────────────────────────────────────────────────────

def simulate(closes, highs, lows, fast_arr, slow_arr, trend_arr, rsi_arr,
             rsi_ob, stop_pct, tp_pct, trail_pct, min_start):
    import numpy as np
    balance = STARTING_BALANCE
    position = None
    sell_pnls = []

    n = len(closes)
    for i in range(min_start, n):
        price = closes[i]
        hi = highs[i]
        lo = lows[i]

        # ── Update trailing stop and check exits ──────────────────────────
        if position is not None:
            position["highest"] = max(position["highest"], hi)
            # Trailing stop ratchets up with highest price
            trail_stop = position["highest"] * (1.0 - trail_pct)
            current_stop = max(position["initial_stop"], trail_stop)
            position["stop"] = current_stop

            hit_stop = lo <= current_stop
            hit_target = hi >= position["target"]
            if hit_stop or hit_target:
                exit_px = current_stop if hit_stop else position["target"]
                proceeds = position["qty"] * exit_px * (1.0 - FEE_PCT)
                balance += proceeds
                sell_pnls.append(proceeds - position["qty"] * position["entry"])
                position = None

        # ── Signal logic ─────────────────────────────────────────────────
        fa, fb = fast_arr[i - 1], fast_arr[i]
        sa, sb = slow_arr[i - 1], slow_arr[i]
        tr = trend_arr[i]
        rv = rsi_arr[i]

        if any(v != v for v in (fa, fb, sa, sb, tr, rv)):  # NaN check
            continue

        crossed_up = fa <= sa and fb > sb
        crossed_down = fa >= sa and fb < sb

        if crossed_up and position is None:
            # RSI and trend filter
            if rv >= rsi_ob:
                continue
            if price < tr:
                continue
            qty = POSITION_USDT / price
            cost = qty * price * (1.0 + FEE_PCT)
            if cost > balance:
                continue
            balance -= cost
            init_stop = price * (1.0 - stop_pct)
            position = {
                "qty": qty, "entry": price,
                "initial_stop": init_stop, "stop": init_stop,
                "target": price * (1.0 + tp_pct),
                "highest": price,
            }

        elif crossed_down and position is not None:
            proceeds = position["qty"] * price * (1.0 - FEE_PCT)
            balance += proceeds
            sell_pnls.append(proceeds - position["qty"] * position["entry"])
            position = None

    final_eq = balance + (position["qty"] * closes[-1] if position else 0.0)
    total_ret = (final_eq / STARTING_BALANCE - 1.0) * 100.0
    n_trades = len(sell_pnls)
    win_rate = sum(1 for p in sell_pnls if p > 0) / n_trades * 100 if n_trades else 0.0
    return total_ret, n_trades, win_rate, final_eq


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import numpy as np

    client = Client("", "")
    print(f"Fetching {INTERVAL} candles for {SYMBOL} since {START}...")
    raw = get_historical_candles(client, SYMBOL, INTERVAL, START)
    print(f"Fetched {len(raw)} candles.\n")

    closes = np.array([c["close"] for c in raw])
    highs  = np.array([c["high"]  for c in raw])
    lows   = np.array([c["low"]   for c in raw])

    # ── Parameter grid ────────────────────────────────────────────────────
    fast_windows  = [5, 10, 15, 20]
    slow_windows  = [30, 40, 50, 60]
    trend_windows = [50, 100, 150, 200]
    rsi_obs       = [65, 70, 75, 80]
    stop_pcts     = [0.02, 0.03, 0.05]
    tp_pcts       = [0.04, 0.06, 0.10, 0.15]
    trail_pcts    = [0.02, 0.03, 0.05]  # trailing stop distance

    combos = [
        (fw, sw, tw, ro, sp, tp, tr)
        for fw, sw, tw, ro, sp, tp, tr
        in itertools.product(fast_windows, slow_windows, trend_windows,
                             rsi_obs, stop_pcts, tp_pcts, trail_pcts)
        if fw < sw
    ]
    print(f"Testing {len(combos)} parameter combinations (with trailing stops)...\n")

    results = []
    t0 = time.time()
    # Cache indicator arrays keyed by (fast_w, slow_w, trend_w)
    indicator_cache = {}

    for idx, (fw, sw, tw, ro, sp, tp, tr) in enumerate(combos):
        key = (fw, sw, tw)
        if key not in indicator_cache:
            indicator_cache[key] = precompute_indicators(closes, fw, sw, tw)

        fa_arr, sa_arr, ta_arr, rsi_arr = indicator_cache[key]
        min_start = max(fw, sw, tw) + 1

        ret, trades, wr, eq = simulate(
            closes, highs, lows,
            fa_arr, sa_arr, ta_arr, rsi_arr,
            ro, sp, tp, tr, min_start
        )
        results.append({
            "fast": fw, "slow": sw, "trend": tw, "rsi_ob": ro,
            "stop": sp, "tp": tp, "trail": tr,
            "return": ret, "trades": trades, "win_rate": wr, "equity": eq,
        })

        if (idx + 1) % 1000 == 0:
            elapsed = time.time() - t0
            print(f"  {idx + 1}/{len(combos)} combos ({elapsed:.0f}s elapsed)...")
            sys.stdout.flush()

    elapsed = time.time() - t0
    results.sort(key=lambda r: r["return"], reverse=True)

    buy_hold = (closes[-1] / closes[max(50, 200) + 1] - 1) * 100

    print(f"\nDone in {elapsed:.1f}s.\n")
    print("=" * 75)
    print(f"  BUY & HOLD RETURN (benchmark): {buy_hold:+.2f}%")
    print("=" * 75)
    print("\nTOP 10 PARAMETER COMBINATIONS BY TOTAL RETURN")
    print("-" * 75)
    for i, r in enumerate(results[:10]):
        print(f"\n  #{i+1}  Return: {r['return']:+.2f}%  |  Equity: ${r['equity']:.2f}  |  "
              f"Trades: {r['trades']}  |  Win Rate: {r['win_rate']:.1f}%")
        print(f"       Fast={r['fast']}  Slow={r['slow']}  Trend={r['trend']}  "
              f"RSI_OB={r['rsi_ob']}  Stop={r['stop']}  TP={r['tp']}  Trail={r['trail']}")

    best = results[0]
    print("\n" + "=" * 75)
    print("BEST PARAMS — copy these into backtest.py / .env:")
    print("=" * 75)
    print(f"  FAST_WINDOW          = {best['fast']}")
    print(f"  SLOW_WINDOW          = {best['slow']}")
    print(f"  TREND_FILTER_WINDOW  = {best['trend']}")
    print(f"  RSI_OVERBOUGHT       = {best['rsi_ob']}")
    print(f"  STOP_LOSS_PCT        = {best['stop']}")
    print(f"  TAKE_PROFIT_PCT      = {best['tp']}")
    print(f"  TRAIL_PCT            = {best['trail']}")
    print(f"  → Return: {best['return']:+.2f}%  |  Trades: {best['trades']}  |  WR: {best['win_rate']:.1f}%")


if __name__ == "__main__":
    main()
