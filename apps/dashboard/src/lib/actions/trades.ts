"use server";

import { z } from "zod";
import { requireSession } from "@/lib/session";
import {
  fetchFilterOptions,
  fetchTradesPage,
  type TradeQuery,
} from "@/db/queries";
import type { TradeRow } from "@/db/schema";

export interface TradeDTO {
  id: string;
  asset: string;
  direction: string;
  strategy: string | null;
  qty: number;
  entryPrice: number;
  exitPrice: number | null;
  stopPrice: number | null;
  pnl: number | null;
  status: string;
  exitReason: string | null;
  openedAt: string; // ISO
  closedAt: string | null; // ISO
}

export interface TradesResult {
  rows: TradeDTO[];
  total: number;
  page: number;
  pageSize: number;
  pageCount: number;
}

const querySchema = z.object({
  asset: z.string().optional(),
  strategy: z.string().optional(),
  direction: z.enum(["all", "long", "short"]).optional(),
  exitReason: z.string().optional(),
  outcome: z.enum(["all", "win", "loss"]).optional(),
  dateFrom: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  dateTo: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  sortKey: z
    .enum([
      "openedAt",
      "closedAt",
      "asset",
      "strategy",
      "direction",
      "qty",
      "entryPrice",
      "exitPrice",
      "pnl",
      "status",
    ])
    .optional(),
  sortDir: z.enum(["asc", "desc"]).optional(),
  page: z.number().int().positive().optional(),
  pageSize: z.number().int().positive().max(200).optional(),
});

function toDTO(r: TradeRow): TradeDTO {
  return {
    id: r.id,
    asset: r.asset,
    direction: r.direction,
    strategy: r.strategy,
    qty: r.qty,
    entryPrice: r.entryPrice,
    exitPrice: r.exitPrice,
    stopPrice: r.stopPrice,
    pnl: r.pnl,
    status: r.status,
    exitReason: r.exitReason,
    openedAt: r.openedAt.toISOString(),
    closedAt: r.closedAt ? r.closedAt.toISOString() : null,
  };
}

export async function getTrades(rawQuery: unknown): Promise<TradesResult> {
  await requireSession();
  const q = (querySchema.safeParse(rawQuery).data ?? {}) as TradeQuery;
  const { rows, total, page, pageSize } = await fetchTradesPage(q);
  return {
    rows: rows.map(toDTO),
    total,
    page,
    pageSize,
    pageCount: Math.max(1, Math.ceil(total / pageSize)),
  };
}

export async function getTradeFilterOptions(): Promise<{
  assets: string[];
  strategies: string[];
  exitReasons: string[];
}> {
  await requireSession();
  return fetchFilterOptions();
}
