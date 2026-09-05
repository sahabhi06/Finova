# Finova — Risk-Gated Algo Trading Bot

A fully working SMA-crossover + RSI-filter trading bot (Finova) with a hard-coded
risk engine, complete audit trail, and advanced risk management features
like ATR-based volatility sizing and trailing stop-losses. This is real,
running code — not a demo — it places real orders against whichever API base URL your
config resolves to. The only thing separating "testnet" from "live" is
two environment variables (see `config.py`), by design.

## Files

| File | Job |
|---|---|
| `config.py` | Loads env vars, enforces the testnet/live safety switch |
| `market_data.py` | Fetches and formats candles from Binance |
| `strategy.py` | Pure functions: SMA, RSI, signal generation |
| `risk.py` | The risk gate — every signal must pass this before an order fires |
| `executor.py` | Places orders with idempotency keys and mandatory stop-losses |
| `audit.py` | SQLite logging of every loop iteration |
| `scheduler.py` | The main loop — run this to start the bot |
| `grid_search.py` | Vectorized parameter optimizer to find best strategy settings |
| `backtest.py` | Test the strategy against 90 days of historical data first |
| `test_strategy.py` | Unit tests for strategy.py and risk.py, no network needed |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: fill in your free testnet keys from testnet.binance.vision
export $(cat .env | grep -v '^#' | xargs)
```

## Run in this order

1. **Unit tests first, always** — proves the math is right before anything touches a network:
   ```bash
   python test_strategy.py
   ```

2. **Parameter Optimization** — find the best parameters for the current market using NumPy for high-speed simulation:
   ```bash
   python grid_search.py
   ```

3. **Backtest** — see how this strategy would have done over the last 90 days,
   compared honestly against just holding the asset:
   ```bash
   python backtest.py
   ```

4. **Run live on testnet** — real loop, fake money:
   ```bash
   python scheduler.py
   ```
   Watch the console output and the `audit_log.db` file (open with any
   SQLite viewer, or start the dashboard server with `python dashboard.py`).
   Let this run for at least a week before trusting it.

5. **Going live** (only after step 4 has run cleanly for a while, and only
   with money you're fully prepared to lose):
   ```bash
   export TRADING_MODE=live
   export I_UNDERSTAND_THIS_USES_REAL_MONEY=yes
   export BINANCE_API_KEY=your_real_key
   export BINANCE_API_SECRET=your_real_secret
   python scheduler.py
   ```
   The bot will print a warning and require you to type a confirmation
   phrase before it starts. This is intentional friction.

## Advanced Features Implemented
- **Grid Search**: Evaluates over 9000 parameter combinations in ~80 seconds to find optimal settings (SMA windows, RSI thresholds, Take Profit, Trailing Stop percentages).
- **Trailing Stop-Loss**: Instead of a fixed stop, the stop-loss ratchets upwards as the price rises in our favor, locking in profits.
- **ATR-Based Position Sizing**: Volatility-adjusted bet sizes. The bot risks exactly `RISK_PER_TRADE_PCT` of your account balance per trade (e.g., smaller position sizes when markets are highly volatile).

## Tuning the risk policy

All the numbers in `config.POLICY` come from environment variables
(`MAX_POSITION_USDT`, `DAILY_LOSS_CAP_USDT`, etc. — see `.env.example`).
Change the *numbers* freely. Do not remove the *checks* in `risk.py` —
that file is what keeps a bad day from becoming a ruinous one.

## What this doesn't do (yet), if you want to keep building

- No multi-symbol support (one symbol per running instance).
- Needs advanced error recovery for exchange rate limits and socket disconnects.

Any of these are reasonable next steps once the basic loop has proven
itself in testnet and paper trading.
