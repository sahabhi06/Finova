from strategy import sma, rsi, generate_signal, atr
from risk import (check_risk, compute_stop_loss_price, compute_trailing_stop_price,
                  compute_atr_position_size)


def make_candles(closes):
    return [{"close": c, "open": c, "high": c, "low": c, "volume": 1} for c in closes]


def make_candles_ohlc(ohlc_list):
    """Create candles from (open, high, low, close) tuples."""
    return [{"open": o, "high": h, "low": l, "close": c, "volume": 1}
            for o, h, l, c in ohlc_list]


def test_sma_basic():
    candles = make_candles([1, 2, 3, 4, 5])
    result = sma(candles, window=3)
    assert result[:2] == [None, None]
    assert result[2] == 2.0   # (1+2+3)/3
    assert result[3] == 3.0   # (2+3+4)/3
    assert result[4] == 4.0   # (3+4+5)/3
    print("test_sma_basic: PASS")


def test_rsi_all_gains_is_100():
    candles = make_candles(list(range(1, 20)))  # strictly increasing
    result = rsi(candles, window=14)
    assert result[14] == 100.0
    print("test_rsi_all_gains_is_100: PASS")


def test_rsi_all_losses_is_0():
    candles = make_candles(list(range(20, 1, -1)))  # strictly decreasing
    result = rsi(candles, window=14)
    assert result[14] == 0.0
    print("test_rsi_all_losses_is_0: PASS")


def test_rsi_filter_blocks_pure_uptrend_crossover():
    # 15 perfectly flat candles then ONE rise: this creates an SMA
    # crossover, but every prior price change is zero (no losses at
    # all), which pins RSI at exactly 100 -- correctly overbought.
    # This demonstrates the RSI filter doing its job, not a bug.
    flat = [100] * 15
    candles = make_candles(flat + [101])
    signal = generate_signal(candles, fast_window=3, slow_window=10, rsi_window=14)
    assert signal["action"] == "HOLD", signal
    assert "overbought" in signal["reason"]
    print(f"test_rsi_filter_blocks_pure_uptrend_crossover: PASS ({signal})")


def test_generate_signal_detects_crossover_with_healthy_rsi():
    # Realistic noisy data (both up and down moves) so RSI isn't
    # pinned at an extreme, then a rise that triggers the crossover --
    # this should clear the RSI filter and fire a BUY.
    noisy = [102, 100, 101.5, 99.5, 100.5, 98.5, 100, 98.2, 99.5, 98, 99, 97.8, 98.5, 97.5, 98]
    candles = make_candles(noisy + [101])
    signal = generate_signal(candles, fast_window=3, slow_window=10, rsi_window=14)
    assert signal["action"] == "BUY", signal
    print(f"test_generate_signal_detects_crossover_with_healthy_rsi: PASS ({signal})")


def test_generate_signal_no_false_positive_on_flat_data():
    # Perfectly flat prices should never produce a crossover.
    candles = make_candles([100] * 20)
    signal = generate_signal(candles, fast_window=3, slow_window=10, rsi_window=14)
    assert signal["action"] == "HOLD", signal
    print("test_generate_signal_no_false_positive_on_flat_data: PASS")


def test_generate_signal_holds_with_insufficient_data():
    candles = make_candles([100, 101, 102])
    signal = generate_signal(candles, fast_window=10, slow_window=30)
    assert signal["action"] == "HOLD"
    assert "Not enough data" in signal["reason"]
    print("test_generate_signal_holds_with_insufficient_data: PASS")


def test_risk_gate_blocks_over_loss_cap():
    signal = {"action": "BUY"}
    session = {"realized_pnl_today": -150, "trades_today": 1, "has_open_position": False}
    policy = {"max_position_usdt": 50, "daily_loss_cap_usdt": 100,
              "max_trades_per_day": 10, "stop_loss_pct": 0.03}
    result = check_risk(signal, "BTCUSDT", session, policy, {"BTCUSDT"})
    assert result["passed"] is False
    failed_rules = [c["rule"] for c in result["checks"] if not c["passed"]]
    assert "daily_loss_cap" in failed_rules
    print(f"test_risk_gate_blocks_over_loss_cap: PASS (blocked on {failed_rules})")


def test_risk_gate_blocks_over_trade_count():
    signal = {"action": "BUY"}
    session = {"realized_pnl_today": 0, "trades_today": 10, "has_open_position": False}
    policy = {"max_position_usdt": 50, "daily_loss_cap_usdt": 100,
              "max_trades_per_day": 10, "stop_loss_pct": 0.03}
    result = check_risk(signal, "BTCUSDT", session, policy, {"BTCUSDT"})
    assert result["passed"] is False
    print("test_risk_gate_blocks_over_trade_count: PASS")


def test_risk_gate_blocks_unknown_symbol():
    signal = {"action": "BUY"}
    session = {"realized_pnl_today": 0, "trades_today": 0, "has_open_position": False}
    policy = {"max_position_usdt": 50, "daily_loss_cap_usdt": 100,
              "max_trades_per_day": 10, "stop_loss_pct": 0.03}
    result = check_risk(signal, "DOGEUSDT", session, policy, {"BTCUSDT"})
    assert result["passed"] is False
    print("test_risk_gate_blocks_unknown_symbol: PASS")


def test_risk_gate_passes_clean_state():
    signal = {"action": "BUY"}
    session = {"realized_pnl_today": 0, "trades_today": 0, "has_open_position": False}
    policy = {"max_position_usdt": 50, "daily_loss_cap_usdt": 100,
              "max_trades_per_day": 10, "stop_loss_pct": 0.03}
    result = check_risk(signal, "BTCUSDT", session, policy, {"BTCUSDT"})
    assert result["passed"] is True
    print("test_risk_gate_passes_clean_state: PASS")


def test_stop_loss_math():
    sl = compute_stop_loss_price(100, 0.03, "BUY")
    assert sl == 97.0
    sl2 = compute_stop_loss_price(100, 0.03, "SELL")
    assert sl2 == 103.0
    print("test_stop_loss_math: PASS")


# --- New tests for ATR, trailing stop, and position sizing ---

def test_atr_basic():
    """ATR on candles with known true ranges."""
    # 16 candles (need at least window+1 = 15 for ATR(14))
    # Using OHLC where true range = high - low = 2 for every candle
    ohlc = [(99, 101, 99, 100)] * 16
    candles = make_candles_ohlc(ohlc)
    result = atr(candles, window=14)
    # First 14 indices should be None, index 14 should be 2.0
    assert result[13] is None
    assert result[14] is not None
    assert abs(result[14] - 2.0) < 0.01, f"Expected ~2.0, got {result[14]}"
    print(f"test_atr_basic: PASS (ATR[14]={result[14]:.4f})")


def test_trailing_stop_ratchets_up():
    """Trailing stop should rise as highest_price rises."""
    entry = 100.0
    trail_pct = 0.03

    # At entry: stop = 100 * 0.97 = 97.0
    stop1 = compute_trailing_stop_price(entry, 100.0, trail_pct, "BUY")
    assert stop1 == 97.0, f"Expected 97.0, got {stop1}"

    # Price rises to 110: stop = 110 * 0.97 = 106.7
    stop2 = compute_trailing_stop_price(entry, 110.0, trail_pct, "BUY")
    assert stop2 == 106.7, f"Expected 106.7, got {stop2}"

    # Stop should never go DOWN — even if price falls back, the stop
    # is computed from highest_price, not current price.
    assert stop2 > stop1, "Trailing stop must ratchet upward"
    print(f"test_trailing_stop_ratchets_up: PASS (97.0 -> 106.7)")


def test_trailing_stop_never_below_initial():
    """Even with highest_price = entry, trailing stop >= initial fixed stop."""
    entry = 100.0
    trail_pct = 0.03
    stop = compute_trailing_stop_price(entry, entry, trail_pct, "BUY")
    initial = compute_stop_loss_price(entry, trail_pct, "BUY")
    assert stop >= initial, f"Trailing stop {stop} should be >= initial {initial}"
    print("test_trailing_stop_never_below_initial: PASS")


def test_atr_position_sizing():
    """Position size scales inversely with volatility."""
    balance = 1000.0
    risk_pct = 0.02  # 2% risk => $20 dollar risk
    max_pos = 10000.0  # high cap so we can see the raw sizing
    price = 50000.0

    # Calm market: ATR stop = $500
    # qty = 20/500 = 0.04, usdt = 0.04 * 50000 = $2000
    size_calm = compute_atr_position_size(balance, risk_pct, 500.0, price, max_pos)
    assert abs(size_calm - 2000.0) < 0.01, f"Expected ~$2000, got {size_calm}"

    # Volatile market: ATR stop = $2000
    # qty = 20/2000 = 0.01, usdt = 0.01 * 50000 = $500
    size_volatile = compute_atr_position_size(balance, risk_pct, 2000.0, price, max_pos)
    assert abs(size_volatile - 500.0) < 0.01, f"Expected ~$500, got {size_volatile}"

    assert size_calm > size_volatile, (
        f"Calm market size ({size_calm}) should be > volatile ({size_volatile})")

    # With a cap below both, should clamp
    size_capped = compute_atr_position_size(balance, risk_pct, 500.0, price, 100.0)
    assert size_capped == 100.0, f"Should be capped at 100, got {size_capped}"

    print(f"test_atr_position_sizing: PASS (calm=${size_calm:.0f}, volatile=${size_volatile:.0f}, capped=${size_capped:.0f})")


def test_atr_position_sizing_fallback():
    """When ATR is None, fall back to max position size."""
    size = compute_atr_position_size(1000, 0.02, None, 50000, 100)
    assert size == 100, f"Expected fallback to 100, got {size}"
    print("test_atr_position_sizing_fallback: PASS")


if __name__ == "__main__":
    test_sma_basic()
    test_rsi_all_gains_is_100()
    test_rsi_all_losses_is_0()
    test_rsi_filter_blocks_pure_uptrend_crossover()
    test_generate_signal_detects_crossover_with_healthy_rsi()
    test_generate_signal_no_false_positive_on_flat_data()
    test_generate_signal_holds_with_insufficient_data()
    test_risk_gate_blocks_over_loss_cap()
    test_risk_gate_blocks_over_trade_count()
    test_risk_gate_blocks_unknown_symbol()
    test_risk_gate_passes_clean_state()
    test_stop_loss_math()
    # New tests
    test_atr_basic()
    test_trailing_stop_ratchets_up()
    test_trailing_stop_never_below_initial()
    test_atr_position_sizing()
    test_atr_position_sizing_fallback()
    print("\nAll tests passed.")

