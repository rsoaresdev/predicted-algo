"use server";

import { requireSession } from "@/lib/session";
import { fetchClosedTrades, fetchDailyMetrics, fetchOpenTrades } from "@/db/queries";
import {
  cagr,
  maxDrawdown,
  profitFactor,
  sharpe,
  totalReturn,
  winRate,
  yearsBetween,
} from "@/lib/metrics";

export interface EquityPoint {
  date: string;
  equity: number;
}
export interface DrawdownPoint {
  date: string;
  drawdown: number; // negative fraction, e.g. -0.08
}

export interface OverviewData {
  kpis: {
    currentEquity: number;
    totalReturn: number; // fraction
    cagr: number; // fraction
    sharpe: number;
    maxDrawdown: number; // negative fraction
    profitFactor: number; // may be Infinity
    winRate: number; // fraction
    numClosedTrades: number;
    openPositions: number;
    realizedPnl: number;
    currentDrawdown: number; // negative fraction
  };
  equityCurve: EquityPoint[];
  drawdownCurve: DrawdownPoint[];
  hasData: boolean;
}

export async function getOverview(): Promise<OverviewData> {
  await requireSession();

  const [metrics, closed, open] = await Promise.all([
    fetchDailyMetrics(),
    fetchClosedTrades(),
    fetchOpenTrades(),
  ]);

  const withEquity = metrics.filter(
    (m): m is typeof m & { endingEquity: number } => m.endingEquity != null,
  );

  const equityCurve: EquityPoint[] = withEquity.map((m) => ({
    date: m.date,
    equity: m.endingEquity,
  }));
  const equitySeries = equityCurve.map((p) => p.equity);

  // Drawdown series from the equity curve (running peak).
  let peak = -Infinity;
  let currentDrawdown = 0;
  const drawdownCurve: DrawdownPoint[] = equityCurve.map((p) => {
    if (p.equity > peak) peak = p.equity;
    const dd = peak > 0 ? p.equity / peak - 1 : 0;
    currentDrawdown = dd;
    return { date: p.date, drawdown: dd };
  });

  const closedPnls = closed
    .map((t) => t.pnl)
    .filter((p): p is number => p != null);

  const first = withEquity[0];
  const last = withEquity[withEquity.length - 1];
  const initialEquity = first?.startingEquity ?? first?.endingEquity ?? 0;
  const finalEquity = last?.endingEquity ?? 0;
  const years =
    first && last
      ? yearsBetween(new Date(`${first.date}T00:00:00Z`), new Date(`${last.date}T00:00:00Z`))
      : 0;

  return {
    kpis: {
      currentEquity: finalEquity,
      totalReturn: totalReturn(initialEquity, finalEquity),
      cagr: cagr(initialEquity, finalEquity, years),
      sharpe: sharpe(equitySeries),
      maxDrawdown: maxDrawdown(equitySeries),
      profitFactor: profitFactor(closedPnls),
      winRate: winRate(closedPnls),
      numClosedTrades: closed.length,
      openPositions: open.length,
      realizedPnl: closedPnls.reduce((a, b) => a + b, 0),
      currentDrawdown,
    },
    equityCurve,
    drawdownCurve,
    hasData: equityCurve.length > 0,
  };
}
