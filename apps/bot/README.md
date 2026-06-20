# predicted-algo — multi-asset trading bot engine

A modular Python trading engine for **SPY, QQQ, GLD, USO, SMH** built on the
modern [`alpaca-py`](https://github.com/alpacahq/alpaca-py) SDK, with strict
ATR-based risk management and PostgreSQL persistence.

## Strategies

| Asset | Strategy | Timeframe | Logic |
|-------|----------|-----------|-------|
| SPY | Trend following (long-only) | 1 hour | 50 EMA / 200 EMA — long while fast > slow, flat otherwise |
| QQQ | Trend following (long-only) | 1 hour | Same |
| GLD | Trend following (long-only) | 1 hour | Same |
| USO | Trend following (long-only) | 1 hour | Same |
| SMH | Trend following (long-only) | 1 hour | Same |

**These assignments were chosen by a systematic optimisation, not assumed.**
`optimize.py` ran 120 strategy/parameter combinations over **2 years** of real
Alpaca data and **trend following won on every asset** — beating both mean
reversion (which the mandatory 1-ATR stop makes structurally unviable: counter-
trend entries get stopped on the overshoot before reverting, ~24% win rate,
negative Sharpe) and momentum. Among *statistically robust* candidates (≥30
trades, so the Sharpe is trustworthy rather than a 10-trade fluke), the per-asset
winner was the **same** config — 1-hour 50/200 EMA, long-only — so the bot runs
it across the board. Per-asset 2-year Sharpe: SPY 0.61, QQQ 1.24, GLD 1.56,
USO 0.61, SMH 1.20. (A faster 20/50 EMA was trialled for more activity but the
1-year backtest showed it worse — portfolio Sharpe 1.49 vs 2.22 for ~2× the
trades — so 50/200 was kept.) The mean-reversion and momentum strategies remain
in the registry for experimentation. See
[Strategy rationale & caveats](#strategy-rationale--caveats).

## Risk management

- **ATR position sizing:** `qty = (equity × 1%) / ATR`, so **1 ATR of price
  movement equals exactly 1% of account equity**.
- **Hard 1% stop:** because of the sizing rule, the protective stop sits exactly
  **1 ATR** from entry — a 1-ATR adverse move is the maximum 1%-of-equity loss.
  It's attached to the entry as an Alpaca OTO (one-triggers-other) stop order, so
  protection exists at the broker even if the bot process dies.
- **Correlation filter:** a new **SMH long is blocked while both SPY and QQQ are
  already long**.
- Sizing is additionally capped by available buying power and floored to whole
  shares (stop orders can't be fractional).

## Project layout

```
config.py               # .env loading + per-asset strategy specs
main.py                 # live CLI entrypoint (--init-db / --once / --live / --dry-run)
backtester.py           # 1-year backtest, walk-forward, report + Sharpe gate
data/broker.py          # AlpacaBroker: wraps alpaca-py trading + data clients
indicators/             # SMA, EMA, ATR (Wilder), rolling std/high/low/volume
strategies/             # base + mean_reversion / trend_following / momentum + factory
risk/risk_manager.py    # ATR sizing, hard stop, correlation filter (pure Python)
engine/engine.py        # the live orchestration loop
database/               # SQLAlchemy models, engine/session, repository, uuidv7 setup
sql/uuidv7_fallback.sql # reference DDL for PostgreSQL < 18
tests/test_risk.py      # unit tests for the risk math (stdlib only)
tests/test_backtester.py# backtester tests (synthetic data; needs pandas)
```

The engine never imports the SDK directly — all broker/market-data access goes
through `AlpacaBroker` in `data/broker.py`. That single seam keeps the strategy,
risk, and orchestration code provider-agnostic and easy to stub in tests.

## Database schema

- **`trades`** — `id uuid PK DEFAULT uuidv7()`, asset, direction, strategy, qty
  (position size), entry_price, exit_price, stop_price, pnl, status (`open`/`closed`),
  exit_reason, opened_at (trade timestamp), closed_at. One row per round-trip:
  inserted on entry, updated on exit with the realised P&L.
- **`daily_metrics`** — date (unique), starting/ending equity, realized_pnl,
  num_trades, wins, losses, win_rate, return_pct.

`uuidv7()` is native in **PostgreSQL 18+**. On older servers, `init_db()` detects
the version via `server_version_num` and installs a tested plpgsql `uuidv7()`
function automatically (see `sql/uuidv7_fallback.sql`).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then fill in Alpaca keys + Postgres credentials
createdb trading_bot        # or point DATABASE_URL at an existing database

python main.py --init-db    # create the schema
```

## Running

```bash
python main.py --once       # one evaluation cycle (great for a cron / smoke test)
python main.py              # continuous loop; DRY_RUN comes from .env (default true)
python main.py --live       # force real order submission
```

`DRY_RUN=true` (the default) computes and logs every signal, size, and stop
**without submitting any orders** — verify behaviour against the paper endpoint
before flipping to `--live`.

## Backtesting

`backtester.py` validates the three strategies over ~1 year of Alpaca history,
reusing the **same** strategy, risk, and indicator code as the live engine.

```bash
python backtester.py                                   # 1 year, $100k/asset, 4 folds
python backtester.py --years 1 --slippage 0.0005 --folds 4 --capital 100000
```

What it does:

- **Bar-by-bar simulation** with **0.05% slippage** on every fill, intrabar
  **hard-stop** checks (the live 1-ATR / 1%-equity stop), and ATR position sizing.
- A consolidated terminal report with **Win rate, Profit factor, Max drawdown,
  and Sharpe ratio** for each asset *and* the combined portfolio (Sharpe/MaxDD
  computed on a daily mark-to-market equity curve, rf=0, annualised by √252).
- **Simple walk-forward:** the test window is split into sequential out-of-sample
  folds and metrics are reported per fold. The strategies are rule-based with
  fixed parameters, so there is no in-sample re-fitting step — the walk-forward
  checks temporal robustness across rolling periods.
- **Strict gate:** if *any* strategy's full-period Sharpe ratio is negative, the
  report is printed and the script then raises `NegativeSharpeError` and exits
  non-zero.

It fetches extra history before the test window so indicators are fully warmed
up, and runs each asset independently (the live-only SMH/SPY/QQQ correlation
filter is therefore not simulated). Needs valid Alpaca **data** API keys.

## Strategy optimisation

`optimize.py` answers *"which strategy fits each asset?"* with data instead of
guesswork. It fetches **2 years** of history and runs **every** candidate
strategy × parameter combination (trend following / momentum / mean reversion
across several timeframes) through the *same* simulator as the backtester, then
ranks them **per asset** by Sharpe ratio.

```bash
python optimize.py                                  # 2 years, default grid
python optimize.py --years 2 --min-trades 8 --top 6
```

- **Minimum-trade guard:** a candidate must take at least `--min-trades` (default
  8) trades *and* post a positive Sharpe to be "eligible" — so a lucky 1-trade
  run can never win the search (the failure mode that made an earlier SPY config
  look great on a single trade).
- For each asset it prints the ranked table and a **paste-ready
  `ASSET_CONFIGS` block** of the winners. Apply it to `config.py`, then re-run
  `python backtester.py` to confirm the non-negative-Sharpe gate.

This is the systematic, reproducible way the current SPY/QQQ assignments were
chosen — extend any loop in `build_search_space()` to widen the search.

## Tests

```bash
python -m unittest tests.test_risk          # risk math; no third-party deps needed
python -m unittest tests.test_backtester    # backtester logic; needs pandas (skips if absent)
python -m unittest discover -s tests        # everything
```

## Security notes

- All secrets live in `.env`, which is git-ignored. `.env.example` is the only
  committed template and contains no real values.
- Start against the **paper** endpoint (`ALPACA_BASE_URL=https://paper-api.alpaca.markets`).

## Strategy rationale & caveats

The strategy↔asset pairings are matched to each instrument's *typical* behaviour:
range-bound, liquid indices get mean reversion; trending commodities get trend
following; a high-beta sector ETF gets momentum. The design has been tuned to
address the obvious failure modes — but **"best" can only be confirmed by
backtesting on your own data and costs.**

All five assets run the same optimiser-selected strategy: **1-hour 50/200 EMA
trend following, long-only.** The table below records what the 2-year search
found and the per-asset caveats.

| Asset | What the optimiser found (2y) | Remaining caveats |
|-------|-------------------------------|-------------------|
| **SPY** | Trend following dominated; 1h 50/200 gave Sharpe 0.61 over 63 trades with a modest −10.5% drawdown. Mean reversion was firmly negative. | Trend systems lag at turns and whipsaw in range-bound markets; expect give-back at reversals. |
| **QQQ** | 1h 50/200 was the standout: Sharpe **1.24**, 53 trades, −10.9% DD. High-beta tech trends cleanly. | Correlated with SMH (both tech) — they can be long together; the SMH correlation filter partly mitigates the concentration. |
| **GLD** | Best risk-adjusted of all: Sharpe **1.56**, 35 trades, −16.1% DD — gold trended strongly over the window. | Crossovers are late at turns; a sharp reversal after a long run gives back open profit. |
| **USO** | 1h 50/200 (Sharpe 0.61, 69 trades), but carries a steep **−29.6% drawdown**. Daily 20/50 roughly halves the drawdown for fewer trades — a reasonable swap. | **USO holds front-month futures** — an imperfect spot-oil proxy with contango drag. The deep drawdown reflects oil's volatility. |
| **SMH** | 1h 50/200: Sharpe **1.20**, 60 trades, but a large **−32.4% drawdown**. Daily 50/200 posted a higher Sharpe (1.56) with a −9.7% DD on only ~10 trades — better risk-adjusted but thin. | Semis are the most volatile of the five; the hard 1-ATR stop caps per-trade loss but not the equity-curve drawdown across many trades. |

Every knob (EMA periods, timeframe, `allow_short`, and the momentum / mean-reversion
parameters for anyone who re-enables those strategies) lives in `config.py`, so
re-tuning — or swapping a strategy — is a one-line change. Re-run `optimize.py`
to regenerate the recommended config from fresh data.

**Before going live:** backtest each pair with realistic commissions and slippage
(and the contango drag on USO), and confirm the tuned parameters hold up out of
sample.

## Notes & assumptions

- **SDK:** built on the modern [`alpaca-py`](https://github.com/alpacahq/alpaca-py)
  SDK. All trading + market-data access is isolated behind `AlpacaBroker` in
  `data/broker.py`, so the rest of the engine is provider-agnostic.
- **Trend following** runs on **1-hour bars** (50/200 EMA) and is state-based
  (long while 50 EMA > 200 EMA, flat otherwise) rather than firing only on the
  exact crossover bar, so the bot aligns to the prevailing trend even when started
  mid-trend. `allow_short` is off by default — the optimiser found long-only
  outperformed long/short over the test window — but can be flipped per asset.
- **Mean reversion** is still implemented (long-only, 200-SMA regime filter +
  2σ band + RSI < 35 confirmation) but **not assigned to any asset by default** —
  the backtest showed it can't overcome the mandatory 1-ATR stop on these
  instruments. Re-enable it for an asset by setting `strategy: "mean_reversion"`
  in `config.py` if you want to experiment with looser stops or other tickers.
- **Momentum / SMH exit:** the spec defines only the entry. Downside is covered by the hard
  1-ATR stop; the upside exit is a **10-period Donchian trailing stop** (close
  below the prior 10-bar low). Tune `exit_period` in `config.py`.
- **Bar timing:** the engine acts on the latest returned bar and de-dupes per bar
  timestamp. Depending on feed, the most recent bar may still be forming intraday;
  swap in a latest-trade lookup for tick-accurate pricing if needed.

> This is trading software for authorized use against your own Alpaca account.
> Backtest and paper-trade thoroughly before risking real capital.
