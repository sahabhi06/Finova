"""
backtest.py

Full-featured backtester with:
  - SMA crossover + RSI filter + SMA trend filter
  - Trailing stop-loss (stop ratchets up as price rises, locking profits)
  - ATR-based volatility position sizing (smaller bets in wild markets,
    larger bets in calm markets — keeps dollar risk per trade constant)
  - Take-profit target

All parameters are configurable at the top of the file.

Usage:
    python backtest.py
"""

from binance.client import Client
from strategy import generate_signal, atr, compute_atr_stop_distance
from modal.risk import (compute_stop_loss_price, compute_take_profit_price,
                  compute_trailing_stop_price, compute_atr_position_size)
from market_data import get_historical_candles

SYMBOL = "BTCUSDT"
INTERVAL = "15m"
START = "90 days ago UTC"
STARTING_BALANCE_USDT = 1000
FEE_PCT = 0.001

# --- Strategy parameters (grid-search optimised) ---
FAST_WINDOW = 20
SLOW_WINDOW = 40          # was 50
TREND_FILTER_WINDOW = 150 # was 100
RSI_WINDOW = 14
RSI_OVERBOUGHT = 65       # was 70

# --- Risk parameters ---
STOP_LOSS_PCT = 0.02          # was 0.03 — tighter initial stop
TAKE_PROFIT_PCT = 0.15        # was 0.06 — let winners run much further
TRAILING_STOP = True
TRAIL_PCT = 0.05              # was 0.03 — wider trail gives more room to run

# --- Volatility position sizing ---
USE_ATR_SIZING = True
ATR_WINDOW = 14
ATR_MULTIPLIER = 2.0
RISK_PER_TRADE_PCT = 0.02
MAX_POSITION_USDT = 100


def run_backtest():
    client = Client("", "")
    print(f"Fetching historical candles for {SYMBOL} {INTERVAL} since {START}...")
    all_candles = get_historical_candles(client, SYMBOL, INTERVAL, START)
    print(f"Fetched {len(all_candles)} candles.\n")

    balance = STARTING_BALANCE_USDT
    position = None  # {qty, entry_price, stop_price, target_price, highest_price}
    trades = []
    equity_curve = []
    exit_reason_counts = {"trailing_stop": 0, "fixed_stop": 0, "take_profit": 0, "signal": 0}

    lookback = max(TREND_FILTER_WINDOW, SLOW_WINDOW) + 10
    min_start = max(TREND_FILTER_WINDOW, SLOW_WINDOW) + 1

    for i in range(min_start, len(all_candles)):
        window = all_candles[max(0, i + 1 - lookback): i + 1]
        candle = window[-1]
        price = candle["close"]

        # --- Update trailing stop and check exits ---
        if position is not None:
            # Update highest price seen since entry
            position["highest_price"] = max(position["highest_price"], candle["high"])

            # Compute current stop price (trailing or fixed)
            if TRAILING_STOP:
                current_stop = compute_trailing_stop_price(
                    position["entry_price"], position["highest_price"],
                    TRAIL_PCT, "BUY"
                )
                # Never let trailing stop go below the initial fixed stop
                current_stop = max(current_stop, position["initial_stop"])
                position["stop_price"] = current_stop

            hit_stop = candle["low"] <= position["stop_price"]
            hit_target = candle["high"] >= position["target_price"]

            if hit_stop or hit_target:
                exit_price = position["stop_price"] if hit_stop else position["target_price"]
                proceeds = position["qty"] * exit_price * (1 - FEE_PCT)
                balance += proceeds
                pnl = proceeds - (position["qty"] * position["entry_price"])

                if hit_stop:
                    # Distinguish trailing stop from fixed stop
                    if position["stop_price"] > position["initial_stop"]:
                        reason = "trailing_stop"
                    else:
                        reason = "fixed_stop"
                else:
                    reason = "take_profit"

                trades.append({"side": "SELL", "pnl": pnl, "reason": reason,
                               "entry": position["entry_price"], "exit": exit_price})
                exit_reason_counts[reason] += 1
                position = None

        # --- Generate signal ---
        signal = generate_signal(
            window,
            fast_window=FAST_WINDOW,
            slow_window=SLOW_WINDOW,
            rsi_window=RSI_WINDOW,
            rsi_overbought=RSI_OVERBOUGHT,
            trend_filter_window=TREND_FILTER_WINDOW,
        )

        # --- Execute trades ---
        if signal["action"] == "BUY" and position is None:
            # Compute ATR-based position size
            if USE_ATR_SIZING:
                atr_stop_dist = compute_atr_stop_distance(window, ATR_WINDOW, ATR_MULTIPLIER)
                position_size_usdt = compute_atr_position_size(
                    balance, RISK_PER_TRADE_PCT, atr_stop_dist, price, MAX_POSITION_USDT
                )
            else:
                position_size_usdt = MAX_POSITION_USDT

            qty = position_size_usdt / price
            cost = qty * price * (1 + FEE_PCT)
            if cost <= balance:
                balance -= cost
                initial_stop = compute_stop_loss_price(price, STOP_LOSS_PCT, "BUY")
                position = {
                    "qty": qty,
                    "entry_price": price,
                    "stop_price": initial_stop,
                    "initial_stop": initial_stop,
                    "target_price": compute_take_profit_price(price, TAKE_PROFIT_PCT, "BUY"),
                    "highest_price": price,
                }
                trades.append({"side": "BUY", "reason": signal["reason"],
                               "size_usdt": position_size_usdt})

        elif signal["action"] == "SELL" and position is not None:
            proceeds = position["qty"] * price * (1 - FEE_PCT)
            balance += proceeds
            pnl = proceeds - (position["qty"] * position["entry_price"])
            trades.append({"side": "SELL", "pnl": pnl, "reason": "signal",
                           "entry": position["entry_price"], "exit": price})
            exit_reason_counts["signal"] += 1
            position = None

        current_equity = balance + (position["qty"] * price if position else 0)
        equity_curve.append(current_equity)

    # --- Compute final metrics ---
    final_equity = balance + (position["qty"] * all_candles[-1]["close"] if position else 0)
    max_drawdown = 0
    peak = equity_curve[0] if equity_curve else STARTING_BALANCE_USDT
    for eq in equity_curve:
        peak = max(peak, eq)
        max_drawdown = max(max_drawdown, (peak - eq) / peak if peak else 0)

    sell_trades = [t for t in trades if t["side"] == "SELL"]
    winning = [t for t in sell_trades if t["pnl"] > 0]
    buy_hold_return = (all_candles[-1]["close"] / all_candles[min_start]["close"] - 1) * 100

    # Average win and average loss
    wins_pnl = [t["pnl"] for t in sell_trades if t["pnl"] > 0]
    losses_pnl = [t["pnl"] for t in sell_trades if t["pnl"] <= 0]
    avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
    avg_loss = sum(losses_pnl) / len(losses_pnl) if losses_pnl else 0

    # --- Print results ---
    print("=" * 65)
    print("  BACKTEST RESULTS: Advanced Strategy")
    print("=" * 65)
    print(f"  Period:            {START} to now, {INTERVAL} candles")
    print(f"  Starting balance:  ${STARTING_BALANCE_USDT:.2f}")
    print(f"  Final equity:      ${final_equity:.2f}")
    print(f"  Total return:      {(final_equity / STARTING_BALANCE_USDT - 1) * 100:+.2f}%")
    print(f"  Buy & hold return: {buy_hold_return:+.2f}%")
    print(f"  Number of trades:  {len(sell_trades)}")
    print(f"  Win rate:          {(len(winning) / len(sell_trades) * 100) if sell_trades else 0:.1f}%")
    print(f"  Avg win:           ${avg_win:.2f}")
    print(f"  Avg loss:          ${avg_loss:.2f}")
    print(f"  Max drawdown:      {max_drawdown * 100:.2f}%")
    print(f"\n  Exits by reason:")
    print(f"    trailing_stop={exit_reason_counts['trailing_stop']}, "
          f"fixed_stop={exit_reason_counts['fixed_stop']}, "
          f"take_profit={exit_reason_counts['take_profit']}, "
          f"signal={exit_reason_counts['signal']}")
    print(f"\n  Strategy config:")
    print(f"    SMA({FAST_WINDOW}/{SLOW_WINDOW}) + Trend SMA({TREND_FILTER_WINDOW}) + RSI({RSI_WINDOW}, OB={RSI_OVERBOUGHT})")
    print(f"    Trailing stop={TRAILING_STOP} ({TRAIL_PCT*100:.1f}%), TP={TAKE_PROFIT_PCT*100:.1f}%")
    print(f"    ATR sizing={USE_ATR_SIZING} (risk {RISK_PER_TRADE_PCT*100:.1f}%/trade, ATR×{ATR_MULTIPLIER})")
    print("=" * 65)


if __name__ == "__main__":
    run_backtest()