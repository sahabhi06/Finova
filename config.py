"""
config.py

Loads all configuration from environment variables. Nothing here should
ever be hard-coded, especially API keys.

THE SAFETY SWITCH:
This bot defaults to TESTNET. To go live, you must set BOTH:
  - TRADING_MODE=live
  - I_UNDERSTAND_THIS_USES_REAL_MONEY=yes
Missing either one keeps you on testnet, on purpose. This is the single
most important check in the whole project -- it exists to prevent the
most common way people accidentally lose money with a bot like this:
an environment variable left over from testing, pointed at the live API.
"""

import os
from pathlib import Path

# Auto-load .env from the same directory as this file, so users don't
# need to manually export vars on every terminal session.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on shell-exported vars


def _get_bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


TRADING_MODE = os.environ.get("TRADING_MODE", "testnet").strip().lower()
LIVE_CONFIRMATION = _get_bool_env("I_UNDERSTAND_THIS_USES_REAL_MONEY")

IS_LIVE = TRADING_MODE == "live" and LIVE_CONFIRMATION

if TRADING_MODE == "live" and not LIVE_CONFIRMATION:
    raise SystemExit(
        "TRADING_MODE=live was set, but I_UNDERSTAND_THIS_USES_REAL_MONEY was not.\n"
        "Refusing to start in an ambiguous state. Set both env vars explicitly, "
        "or unset TRADING_MODE to fall back to testnet."
    )

API_KEY = os.environ.get(
    "BINANCE_API_KEY" if IS_LIVE else "BINANCE_TESTNET_API_KEY", ""
)
API_SECRET = os.environ.get(
    "BINANCE_API_SECRET" if IS_LIVE else "BINANCE_TESTNET_API_SECRET", ""
)

if not API_KEY or not API_SECRET:
    raise SystemExit(
        f"Missing API credentials for mode='{TRADING_MODE}'. "
        f"Set {'BINANCE_API_KEY / BINANCE_API_SECRET' if IS_LIVE else 'BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET'} "
        "as environment variables before starting."
    )

SYMBOL = os.environ.get("BOT_SYMBOL", "BTCUSDT")
INTERVAL = os.environ.get("BOT_INTERVAL", "15m")
LOOP_SECONDS = int(os.environ.get("BOT_LOOP_SECONDS", "60"))

ALLOWED_SYMBOLS = {SYMBOL}

# --- Strategy parameters (SMA / RSI / trend filter) ---
STRATEGY = {
    "fast_window": int(os.environ.get("FAST_WINDOW", "20")),
    "slow_window": int(os.environ.get("SLOW_WINDOW", "50")),
    "trend_filter_window": int(os.environ.get("TREND_FILTER_WINDOW", "100")),
    "rsi_window": int(os.environ.get("RSI_WINDOW", "14")),
    "rsi_overbought": float(os.environ.get("RSI_OVERBOUGHT", "70")),
}

POLICY = {
    # --- Position sizing ---
    "max_position_usdt": float(os.environ.get("MAX_POSITION_USDT", "100")),
    "daily_loss_cap_usdt": float(os.environ.get("DAILY_LOSS_CAP_USDT", "100")),
    "max_trades_per_day": int(os.environ.get("MAX_TRADES_PER_DAY", "10")),
    # --- Exit rules ---
    "stop_loss_pct": float(os.environ.get("STOP_LOSS_PCT", "0.03")),
    "take_profit_pct": float(os.environ.get("TAKE_PROFIT_PCT", "0.06")),
    # --- Trailing stop (replaces fixed stop once price moves in our favour) ---
    "trailing_stop_enabled": os.environ.get("TRAILING_STOP", "true").lower() in ("1", "true", "yes"),
    "trail_pct": float(os.environ.get("TRAIL_PCT", "0.03")),
    # --- ATR-based position sizing ---
    "atr_sizing_enabled": os.environ.get("ATR_SIZING", "true").lower() in ("1", "true", "yes"),
    "atr_window": int(os.environ.get("ATR_WINDOW", "14")),
    "atr_multiplier": float(os.environ.get("ATR_MULTIPLIER", "2.0")),
    "risk_per_trade_pct": float(os.environ.get("RISK_PER_TRADE_PCT", "0.02")),
}

DB_PATH = os.environ.get("BOT_DB_PATH", "audit_log.db")

print(
    f"[config] mode={'LIVE - REAL MONEY' if IS_LIVE else 'TESTNET (safe)'} "
    f"symbol={SYMBOL} interval={INTERVAL}"
)
