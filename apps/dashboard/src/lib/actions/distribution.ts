"use server";

import { format, startOfMonth, startOfWeek } from "date-fns";
import { requireSession } from "@/lib/session";
import { fetchClosedTrades } from "@/db/queries";

export type Bucket = "week" | "month";

export interface DistributionRow {
  period: string; // bucket label (ISO-ish, sortable)
  label: string; // human label
  wins: number;
  losses: number;
  pnl: number;
  winRate: number; // fraction
  cumulativePnl: number;
}

/** Win/loss counts and P&L bucketed weekly or monthly by close date. */
export async function getDistribution(
  bucket: Bucket = "month",
): Promise<DistributionRow[]> {
  await requireSession();
  const closed = await fetchClosedTrades();

  const groups = new Map<string, { label: string; pnls: number[] }>();
  for (const t of closed) {
    if (t.closedAt == null || t.pnl == null) continue;
    const d = t.closedAt;
    const start =
      bucket === "week" ? startOfWeek(d, { weekStartsOn: 1 }) : startOfMonth(d);
    const period = format(start, "yyyy-MM-dd");
    const label =
      bucket === "week" ? `Wk ${format(start, "MMM d")}` : format(start, "MMM yyyy");
    const g = groups.get(period) ?? { label, pnls: [] };
    g.pnls.push(t.pnl);
    groups.set(period, g);
  }

  let cumulative = 0;
  return [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([period, { label, pnls }]) => {
      const wins = pnls.filter((p) => p > 0).length;
      const losses = pnls.filter((p) => p < 0).length;
      const pnl = pnls.reduce((a, b) => a + b, 0);
      cumulative += pnl;
      return {
        period,
        label,
        wins,
        losses,
        pnl,
        winRate: pnls.length ? wins / pnls.length : 0,
        cumulativePnl: cumulative,
      };
    });
}
