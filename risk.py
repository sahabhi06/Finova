"""
risk.py

THE MOST IMPORTANT FILE IN THIS PROJECT.

The strategy engine's job is "is this a good trade." This file's job is
"even if the strategy is wrong, how bad can today get." Those are
different questions, kept in different code on purpose -- a strategy
tweak should never be able to accidentally weaken a risk limit.

This is pure, deterministic code. No AI judgment calls happen here.
Every signal must pass through check_risk() before an order is placed.
"""

from typing import Dict


def check_risk(signal: Dict, symbol: str, session: Dict, policy: Dict, allowed_symbols: set) -> Dict:
    """
    session expects: {"realized_pnl_today": float, "trades_today": int,
                       "has_open_position": bool}

    Returns:
        {
          "passed": bool,
          "checks": [ {rule, passed, detail}, ... ],
          "position_size_usdt": float,
        }
    """
    checks = []

    checks.append({
        "rule": "known_symbol",
        "passed": symbol in allowed_symbols,
        "detail": f"{symbol} in allowed set" if symbol in allowed_symbols else f"{symbol} not in allowed set {allowed_symbols}",
    })

    within_loss_cap = session["realized_pnl_today"] > -abs(policy["daily_loss_cap_usdt"])
    checks.append({
        "rule": "daily_loss_cap",
        "passed": within_loss_cap,
        "detail": f"realized_pnl_today={session['realized_pnl_today']:.2f}, cap=-{policy['daily_loss_cap_usdt']}",
    })

    under_trade_limit = session["trades_today"] < policy["max_trades_per_day"]
    checks.append({
        "rule": "max_trades_per_day",
        "passed": under_trade_limit,
        "detail": f"trades_today={session['trades_today']}, max={policy['max_trades_per_day']}",
    })

    if signal["action"] == "BUY":
        no_conflicting_position = not session.get("has_open_position", False)
        checks.append({
            "rule": "no_duplicate_position",
            "passed": no_conflicting_position,
            "detail": "already holding a position" if not no_conflicting_position else "flat, ok to enter",
        })

    if signal["action"] == "SELL":
        has_position_to_sell = session.get("has_open_position", False)
        checks.append({
            "rule": "position_exists_to_sell",
            "passed": has_position_to_sell,
            "detail": "no open position to sell" if not has_position_to_sell else "position confirmed",
        })

    position_size_usdt = min(policy["max_position_usdt"], policy["max_position_usdt"])
    checks.append({
        "rule": "position_size_within_max",
        "passed": position_size_usdt <= policy["max_position_usdt"],
        "detail": f"sized at {position_size_usdt}, max {policy['max_position_usdt']}",
    })

    all_passed = all(c["passed"] for c in checks)

    return {
        "passed": all_passed,
        "checks": checks,
        "position_size_usdt": position_size_usdt,
    }


def compute_stop_loss_price(entry_price: float, stop_loss_pct: float, side: str) -> float:
    """
    Every single order this bot places must carry a stop-loss. This is
    not optional and not something the strategy engine can override --
    it's derived purely from POLICY['stop_loss_pct'].
    """
    if side == "BUY":
        return round(entry_price * (1 - stop_loss_pct), 2)
    return round(entry_price * (1 + stop_loss_pct), 2)


def compute_take_profit_price(entry_price: float, take_profit_pct: float, side: str) -> float:
    if side == "BUY":
        return round(entry_price * (1 + take_profit_pct), 2)
    return round(entry_price * (1 - take_profit_pct), 2)


def compute_trailing_stop_price(entry_price: float, highest_price: float,
                                 trail_pct: float, side: str) -> float:
    """
    Trailing stop-loss: the stop price ratchets with the highest price
    seen since entry. For a BUY position, the stop is always
    `highest_price * (1 - trail_pct)`, so as price rises the stop rises
    with it, locking in profits. The stop can never move down.

    Returns the trailing stop price, which is guaranteed to be at least
    as high as the initial fixed stop (entry * (1 - trail_pct)).
    """
    if side == "BUY":
        initial_stop = entry_price * (1 - trail_pct)
        trailing = highest_price * (1 - trail_pct)
        return round(max(initial_stop, trailing), 2)
    else:
        initial_stop = entry_price * (1 + trail_pct)
        trailing = highest_price * (1 + trail_pct)  # highest_price = lowest for shorts
        return round(min(initial_stop, trailing), 2)


def compute_atr_position_size(account_balance: float, risk_per_trade_pct: float,
                               atr_stop_distance: float, current_price: float,
                               max_position_usdt: float) -> float:
    """
    Volatility-based position sizing. Instead of betting a fixed $50
    every trade regardless of whether the market is calm or wild, this
    calculates the position size so that *if the stop-loss is hit*, the
    dollar loss is always `account_balance * risk_per_trade_pct`.

    In calm markets (small ATR), this gives larger positions.
    In volatile markets (large ATR), this gives smaller positions.
    The result is capped at max_position_usdt from the risk policy.

    Returns position size in USDT.
    """
    if atr_stop_distance is None or atr_stop_distance <= 0:
        return max_position_usdt  # fallback to fixed size

    dollar_risk = account_balance * risk_per_trade_pct
    # How many units can we buy so that (qty * atr_stop_distance) = dollar_risk?
    qty = dollar_risk / atr_stop_distance
    position_usdt = qty * current_price
    return min(position_usdt, max_position_usdt)

