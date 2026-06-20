# Go-live assessment — what the bot is, and should you run it (or just buy SP500)?

_2026-06-20. Honest read on the current bot. Backtest = 3y shared-account,
$2k, 0.10% slippage, realistic next-open fills, current default config._

## 1. Everything the bot applies (the full stack)

**Universe & signals**
- 6 long-only ETFs on slow timeframes (cost-robust, survives 0.10–0.15% slippage):
  QQQ (US growth), GLD (gold), SLV (silver), EWG (Europe), EEM (EM), UUP (USD hedge).
- Per-asset strategies: **trend_following** (EMA cross), **macd_trend** (MACD +
  SMA100 trend filter), **momentum** (Donchian breakout + volume confirmation).

**Risk & sizing**
- **ATR sizing**: every trade risks exactly `RISK_PER_TRADE` (1%) of equity.
- **Wider initial stop (1.5 ATR)** — sized so the $ risk stays 1% regardless;
  gives trades room to survive entry noise (the single biggest win-rate gain).
- **Trailing stop**: lock breakeven at +1.5 ATR, then trail 4 ATR behind the peak.
- **MACD-exit gating**: exit only when MACD crosses down *and* price < SMA100.
- **Correlation filter**: ≤ 2 concurrent equity longs (QQQ/EWG/EEM).
- **max_position_fraction 0.33**: each position ≤ 33% of equity (forces diversification).

**Safety & ops**
- **Drawdown circuit-breaker** (halt new entries at −10% from high-water) and
  **daily kill-switch** (−6% realized/day).
- Data-quality guard (no NaN/stale bars), per-cycle heartbeat, alerting
  (log + optional Telegram), startup config validation, software stop driven by
  a live websocket price.
- **Tested and rejected** (kept off): market-regime filter, re-entry cooldown,
  ADX entry filter — all hurt on this book (see STRATEGY_IMPROVEMENTS.md).

**Backtested result (current defaults, 3y):**
**Sharpe 2.45 · CAGR +26% · MaxDD −5.6% · win 28.8% · PF 2.24**, all four
walk-forward folds ≥ 1.70. (Last 1y, used to seed the dashboard: +43.5%.)

## 2. Go-live readiness score: **62 / 100**

Not a knock on the engineering — it's a measure of *proven, deployable edge*.
The number is dominated by one thing: **it has never traded live.**

| Dimension | Score | Why |
|---|---:|---|
| Engineering & ops | 9/10 | Tested (57), guards, alerting, hardened deploy, read-only dashboard |
| Backtest edge | 9/10 | Sharpe 2.45, PF 2.24, 4/4 folds positive |
| Out-of-sample robustness | 6/10 | Walk-forward holds, but tuning was chosen on this same 3y window |
| **Live-proven** | **1/10** | **Zero live/paper track record yet — the big gap** |
| Regime coverage | 4/10 | 2023–26 was mostly a bull run; long-only book thinly tested in a real bear |
| Cost/execution realism | 6/10 | 0.10% slippage & software-stop latency assumed, not yet verified live |

→ weighted ≈ **62/100**. A well-built, genuinely promising system whose edge is
**plausible but unproven**. Headline backtest numbers are optimistic (in-sample
selection + a favourable window); plan on live being meaningfully lower.

**This score is designed to move.** Run the **2-month paper trade** (see
infra/DEPLOY_HETZNER.md). If live tracks the backtest (fills ≈ 0.10% slippage,
drawdown in range, stops firing correctly), "live-proven" and "cost realism"
jump and the score reaches **~80** — then scale real money up gradually.

## 3. Bot vs. just buying SP500

| | Bot (backtest) | SP500 buy-and-hold (typical) |
|---|---|---|
| Return | ~26%/yr | ~10%/yr |
| Sharpe | ~2.45 | ~0.5–0.9 |
| Max drawdown | ~−6% | −25% (2022), −34% (2020), −55% (GFC) |
| Effort / risk | execution, bugs, model decay | none — it *is* the market |
| Proven? | no (backtest only) | yes (a century of it) |

**On paper the bot dominates** — far better risk-adjusted return and a fraction
of the drawdown. **But SP500 is the honest benchmark you must beat with *live*
results**, because it's free, zero-maintenance, and carries no overfitting or
execution risk. A backtested Sharpe of 2.45 that decays to 1.0 live still beats
SP500 comfortably; one that decays to 0.4 does not.

**Recommendation**
1. **Paper-trade 2 months first.** This is non-negotiable — it's how you convert
   the 62 into an 80 (or learn to walk away).
2. **If you deploy real money, only risk capital** — money you can fully afford
   to lose while the edge is unproven. The €2k account is sized right for this.
3. **Benchmark live against SPY every week.** If after the paper run (and a few
   live months) the bot isn't clearly beating SPY on a risk-adjusted basis,
   **SP500 is the rational default** — no shame in it.
4. The realistic split for a do-it-yourself investor: keep the bulk in a low-cost
   SP500/world index, run the bot with a small slice you're treating as a live
   experiment, and let *its own* track record earn a bigger allocation.

**Bottom line:** the bot is good enough to take to paper trading with real
confidence, not yet good enough to bet money you'd miss. Let two months of live
data — not the backtest — decide how much goes online.
