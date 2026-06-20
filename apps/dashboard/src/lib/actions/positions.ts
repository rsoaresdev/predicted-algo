"use server";

import { requireSession } from "@/lib/session";
import { fetchOpenTrades } from "@/db/queries";

export interface OpenPositionDTO {
  id: string;
  asset: string;
  direction: string;
  strategy: string | null;
  qty: number;
  entryPrice: number;
  stopPrice: number | null;
  notional: number; // qty * entry (cost basis)
  openedAt: string; // ISO
}

/**
 * Open positions (status='open'). Returns entry, qty, stop and cost basis.
 *
 * Unrealized P&L is marked on the client against a live price: the dashboard has
 * no broker credentials, so the current price comes from a keyless public quote
 * endpoint (see lib/quotes.ts + actions/quotes.ts), not Alpaca. This action stays
 * a pure DB read; the client polls quotes and computes the mark.
 */
export async function getOpenPositions(): Promise<OpenPositionDTO[]> {
  await requireSession();
  const rows = await fetchOpenTrades();
  return rows.map((r) => ({
    id: r.id,
    asset: r.asset,
    direction: r.direction,
    strategy: r.strategy,
    qty: r.qty,
    entryPrice: r.entryPrice,
    stopPrice: r.stopPrice,
    notional: r.qty * r.entryPrice,
    openedAt: r.openedAt.toISOString(),
  }));
}
