"""
scheduler.py

The main loop. Runs on an interval, and every iteration goes through
the same pipeline:

    fetch candles -> generate_signal -> check_risk -> (maybe) execute -> log

This is the file you actually run: `python scheduler.py`

New vs old:
  - Uses config.STRATEGY for all signal parameters (SMA windows, RSI, trend filter)
  - Updates trailing stop on every loop tick when a position is open
  - Uses config.POLICY["atr_sizing_enabled"] for ATR-based position sizing
"""

import time
import uuid

import config
from market_data import get_client, get_candles, get_current_price
from strategy import generate_signal, compute_atr_stop_distance
from risk import (check_risk, compute_stop_loss_price, compute_trailing_stop_price,
                  compute_atr_position_size)
from executor import place_market_order, place_stop_loss_order
from audit import init_db, log_event


def main():
    if config.IS_LIVE:
        print("=" * 60)
        print("  WARNING: RUNNING IN LIVE MODE. REAL MONEY IS AT RISK.")
        print("=" * 60)
        answer = input("Type 'yes I understand' to continue: ")
        if answer.strip() != "yes I understand":
            print("Confirmation not received. Exiting.")
            return

    client = get_client(config.API_KEY, config.API_SECRET, testnet=not config.IS_LIVE)
    conn = init_db(config.DB_PATH)
    session_id = uuid.uuid4().hex[:8]

    session = {
        "realized_pnl_today": 0.0,
        "trades_today": 0,
        "has_open_position": False,
        "entry_price": None,
        "position_qty": None,
        "highest_price": None,      # Track for trailing stop
        "initial_stop_price": None, # The floor for the trailing stop
        "account_balance": 1000.0,  # Approximate; update on fills
    }

    print(f"[scheduler] session={session_id} symbol={config.SYMBOL} "
          f"interval={config.INTERVAL} loop={config.LOOP_SECONDS}s")
    print(f"[scheduler] strategy={config.STRATEGY}")
    print(f"[scheduler] trailing_stop={config.POLICY['trailing_stop_enabled']} "
          f"atr_sizing={config.POLICY['atr_sizing_enabled']}")

    while True:
        try:
            # Fetch enough candles to cover the largest window + buffer
            lookback = max(
                config.STRATEGY["slow_window"],
                config.STRATEGY["trend_filter_window"]
            ) + 20
            candles = get_candles(client, config.SYMBOL, config.INTERVAL, limit=lookback)
            close_price = candles[-1]["close"]
            high_price = candles[-1]["high"]

            # ── Update trailing stop on open position ─────────────────────
            if session["has_open_position"] and config.POLICY["trailing_stop_enabled"]:
                session["highest_price"] = max(
                    session["highest_price"] or close_price, high_price
                )
                new_stop = compute_trailing_stop_price(
                    session["entry_price"], session["highest_price"],
                    config.POLICY["trail_pct"], "BUY"
                )
                new_stop = max(new_stop, session["initial_stop_price"])
                # In a real system you'd cancel/replace the exchange stop order here.
                # For now, we track in session and use it in sell logic.
                session["current_stop_price"] = new_stop

            # ── Generate signal ───────────────────────────────────────────
            signal = generate_signal(
                candles,
                fast_window=config.STRATEGY["fast_window"],
                slow_window=config.STRATEGY["slow_window"],
                rsi_window=config.STRATEGY["rsi_window"],
                rsi_overbought=config.STRATEGY["rsi_overbought"],
                trend_filter_window=config.STRATEGY["trend_filter_window"],
            )
            risk_result = check_risk(
                signal, config.SYMBOL, session, config.POLICY, config.ALLOWED_SYMBOLS
            )

            order = None
            stop_loss_price = None
            outcome = "no_action"

            if signal["action"] == "HOLD":
                outcome = "held"

            elif not risk_result["passed"]:
                failed = [c["rule"] for c in risk_result["checks"] if not c["passed"]]
                outcome = f"blocked_by_risk_gate: {', '.join(failed)}"
                print(f"[risk] blocked {signal['action']}: {failed}")

            elif signal["action"] == "BUY":
                # ── Compute position size ─────────────────────────────────
                if config.POLICY["atr_sizing_enabled"]:
                    atr_stop_dist = compute_atr_stop_distance(
                        candles,
                        config.POLICY["atr_window"],
                        config.POLICY["atr_multiplier"],
                    )
                    position_usdt = compute_atr_position_size(
                        session["account_balance"],
                        config.POLICY["risk_per_trade_pct"],
                        atr_stop_dist,
                        close_price,
                        config.POLICY["max_position_usdt"],
                    )
                else:
                    position_usdt = risk_result["position_size_usdt"]

                qty = round(position_usdt / close_price, 6)
                result = place_market_order(client, config.SYMBOL, "BUY", qty, session_id)
                if result["success"]:
                    order = result["order"]
                    outcome = "order_placed"
                    stop_loss_price = compute_stop_loss_price(
                        close_price, config.POLICY["stop_loss_pct"], "BUY"
                    )
                    sl_result = place_stop_loss_order(
                        client, config.SYMBOL, "SELL", qty, stop_loss_price, session_id
                    )
                    if not sl_result["success"]:
                        outcome = f"order_placed_but_stop_loss_FAILED: {sl_result['error']}"
                        print(f"[executor] !! stop-loss failed to place: {sl_result['error']}")
                    session["has_open_position"] = True
                    session["entry_price"] = close_price
                    session["position_qty"] = qty
                    session["highest_price"] = close_price
                    session["initial_stop_price"] = stop_loss_price
                    session["current_stop_price"] = stop_loss_price
                    session["trades_today"] += 1
                    session["account_balance"] -= qty * close_price
                else:
                    outcome = f"order_rejected: {result['error']}"
                    print(f"[executor] BUY rejected: {result['error']}")

            elif signal["action"] == "SELL":
                qty = session["position_qty"]
                result = place_market_order(client, config.SYMBOL, "SELL", qty, session_id)
                if result["success"]:
                    order = result["order"]
                    outcome = "order_placed"
                    pnl = (close_price - session["entry_price"]) * qty
                    session["realized_pnl_today"] += pnl
                    session["account_balance"] += qty * close_price
                    session["has_open_position"] = False
                    session["trades_today"] += 1
                    session["highest_price"] = None
                    session["initial_stop_price"] = None
                    session["current_stop_price"] = None
                else:
                    outcome = f"order_rejected: {result['error']}"
                    print(f"[executor] SELL rejected: {result['error']}")

            log_event(
                conn, config.SYMBOL, close_price, signal, risk_result,
                order=order, stop_loss_price=stop_loss_price,
                outcome=outcome, session_pnl_after=session["realized_pnl_today"],
            )

            trailing_info = ""
            if session["has_open_position"] and config.POLICY["trailing_stop_enabled"]:
                trailing_info = (f" trail_stop={session.get('current_stop_price', '?'):.2f}"
                                 f" highest={session.get('highest_price', '?'):.2f}")

            print(f"[loop] {signal['action']:5s} price={close_price:.2f} "
                  f"outcome={outcome} pnl_today={session['realized_pnl_today']:.2f}"
                  f"{trailing_info}")

        except Exception as e:
            # Never let one bad loop iteration crash the whole bot silently.
            print(f"[error] loop iteration failed: {e}")

        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    main()
