/**
 * Performance metrics, ported 1:1 from the bot's backtester so the dashboard
 * reports the same numbers the strategy research uses.
 *
 * Reference: apps/bot/backtester.py
 *   - sharpe_from_daily  (lines 563-570)
 *   - max_drawdown       (lines 581-587)
 *   - _profit_factor     (lines 590-595)
 *   - CAGR               (line 787)
 *   - TRADING_DAYS = 252 (line 60)
 *
 * The equity series passed in is one point per trading day (daily_metrics.
 * ending_equity ordered by date), which already matches the backtester's
 * `equity.resample("1D").last()` step — so no resampling is needed here.
 */

export const TRADING_DAYS = 252;

/** Sample standard deviation (ddof=1 — matches pandas Series.std()). */
export function sampleStd(xs: number[]): number {
  const n = xs.length;
  if (n < 2) return NaN;
  const mean = xs.reduce((a, b) => a + b, 0) / n;
  const ss = xs.reduce((a, b) => a + (b - mean) * (b - mean), 0);
  return Math.sqrt(ss / (n - 1));
}

export function mean(xs: number[]): number {
  if (xs.length === 0) return NaN;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

/**
 * Period-over-period simple returns, like pandas `Series.pct_change().dropna()`.
 * The first element has no predecessor (dropped); pairs with a zero predecessor
 * are skipped to avoid division blow-ups (equity is never legitimately 0).
 */
export function pctChange(series: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < series.length; i++) {
    const prev = series[i - 1];
    if (prev === 0 || Number.isNaN(prev) || Number.isNaN(series[i])) continue;
    out.push(series[i] / prev - 1);
  }
  return out;
}

/**
 * Annualised Sharpe (rf=0) from a daily equity curve.
 * backtester.py:563 — `rets.mean() / rets.std(ddof=1) * sqrt(252)`,
 * returning 0 when there are <2 returns or zero variance.
 */
export function sharpe(
  dailyEquity: number[],
  periodsPerYear = TRADING_DAYS,
): number {
  const rets = pctChange(dailyEquity);
  if (rets.length < 2) return 0;
  const sd = sampleStd(rets);
  if (sd === 0 || Number.isNaN(sd)) return 0;
  return (mean(rets) / sd) * Math.sqrt(periodsPerYear);
}

/**
 * Max drawdown as a negative fraction (e.g. -0.12).
 * backtester.py:581 — `min(equity / cummax - 1)`.
 */
export function maxDrawdown(equity: number[]): number {
  if (equity.length === 0) return 0;
  let runningMax = -Infinity;
  let min = 0;
  for (const v of equity) {
    if (v > runningMax) runningMax = v;
    if (runningMax > 0) {
      const dd = v / runningMax - 1;
      if (dd < min) min = dd;
    }
  }
  return min;
}

/**
 * Profit factor = gross profit / |gross loss|.
 * backtester.py:590 — Infinity when there are profits but no losses, else 0.
 */
export function profitFactor(pnls: number[]): number {
  let grossProfit = 0;
  let grossLoss = 0;
  for (const p of pnls) {
    if (p > 0) grossProfit += p;
    else if (p < 0) grossLoss += -p;
  }
  if (grossLoss === 0) return grossProfit > 0 ? Infinity : 0;
  return grossProfit / grossLoss;
}

/**
 * Compound annual growth rate.
 * backtester.py:787 — `(final/initial)^(1/years) - 1`, guarded for final>0 & years>0.
 */
export function cagr(initial: number, final: number, years: number): number {
  if (final > 0 && years > 0 && initial > 0) {
    return Math.pow(final / initial, 1 / years) - 1;
  }
  return 0;
}

export function totalReturn(initial: number, final: number): number {
  if (!initial) return 0;
  return (final - initial) / initial;
}

/** Win rate as a fraction of closed trades with pnl > 0 (strict, like the bot). */
export function winRate(pnls: number[]): number {
  if (pnls.length === 0) return 0;
  const wins = pnls.filter((p) => p > 0).length;
  return wins / pnls.length;
}

/** Calendar-year span between two dates, used as `years` for CAGR. */
export function yearsBetween(first: Date, last: Date): number {
  const ms = last.getTime() - first.getTime();
  return ms / (365.25 * 24 * 60 * 60 * 1000);
}
