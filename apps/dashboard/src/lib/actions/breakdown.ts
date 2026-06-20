"use server";

import { requireSession } from "@/lib/session";
import { fetchClosedTrades } from "@/db/queries";
import { profitFactor, winRate } from "@/lib/metrics";
import type { TradeRow } from "@/db/schema";

export interface BreakdownRow {
  key: string; // strategy or asset name
  numTrades: number;
  pnl: number;
  winRate: number; // fraction
  profitFactor: number; // may be Infinity
  wins: number;
  losses: number;
}

export interface BreakdownData {
  byStrategy: BreakdownRow[];
  byAsset: BreakdownRow[];
}

function aggregate(
  trades: TradeRow[],
  keyOf: (t: TradeRow) => string,
): BreakdownRow[] {
  const groups = new Map<string, number[]>();
  for (const t of trades) {
    if (t.pnl == null) continue;
    const key = keyOf(t) || "—";
    const arr = groups.get(key) ?? [];
    arr.push(t.pnl);
    groups.set(key, arr);
  }
  return [...groups.entries()]
    .map(([key, pnls]) => ({
      key,
      numTrades: pnls.length,
      pnl: pnls.reduce((a, b) => a + b, 0),
      winRate: winRate(pnls),
      profitFactor: profitFactor(pnls),
      wins: pnls.filter((p) => p > 0).length,
      losses: pnls.filter((p) => p < 0).length,
    }))
    .sort((a, b) => b.pnl - a.pnl);
}

export async function getBreakdown(): Promise<BreakdownData> {
  await requireSession();
  const closed = await fetchClosedTrades();
  return {
    byStrategy: aggregate(closed, (t) => t.strategy ?? "—"),
    byAsset: aggregate(closed, (t) => t.asset),
  };
}
