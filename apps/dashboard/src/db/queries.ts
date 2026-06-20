/**
 * Read-only query layer. Pure SELECTs against the bot tables; all aggregation
 * and derived metrics happen in TypeScript (src/lib/metrics.ts), per the
 * read-only design — nothing here writes.
 */
import { and, asc, count, desc, eq, gt, gte, lt, lte, type SQL } from "drizzle-orm";
import { db } from "./client";
import { dailyMetrics, trades, type TradeRow } from "./schema";

export type TradeOutcome = "all" | "win" | "loss";

export type TradeSortKey =
  | "openedAt"
  | "closedAt"
  | "asset"
  | "strategy"
  | "direction"
  | "qty"
  | "entryPrice"
  | "exitPrice"
  | "pnl"
  | "status";

export interface TradeFilters {
  asset?: string;
  strategy?: string;
  direction?: string; // 'all' | 'long' | 'short'
  exitReason?: string; // 'all' | a specific exit_reason
  outcome?: TradeOutcome;
  dateFrom?: string; // YYYY-MM-DD (inclusive, on opened_at)
  dateTo?: string; // YYYY-MM-DD (inclusive, on opened_at)
}

export interface TradeQuery extends TradeFilters {
  sortKey?: TradeSortKey;
  sortDir?: "asc" | "desc";
  page?: number; // 1-based
  pageSize?: number;
}

const SORT_COLUMNS = {
  openedAt: trades.openedAt,
  closedAt: trades.closedAt,
  asset: trades.asset,
  strategy: trades.strategy,
  direction: trades.direction,
  qty: trades.qty,
  entryPrice: trades.entryPrice,
  exitPrice: trades.exitPrice,
  pnl: trades.pnl,
  status: trades.status,
} as const;

function buildTradeWhere(f: TradeFilters): SQL | undefined {
  const conds: SQL[] = [];
  if (f.asset && f.asset !== "all") conds.push(eq(trades.asset, f.asset));
  if (f.strategy && f.strategy !== "all") conds.push(eq(trades.strategy, f.strategy));
  if (f.direction && f.direction !== "all") conds.push(eq(trades.direction, f.direction));
  if (f.exitReason && f.exitReason !== "all")
    conds.push(eq(trades.exitReason, f.exitReason));
  if (f.outcome === "win") conds.push(gt(trades.pnl, 0));
  if (f.outcome === "loss") conds.push(lt(trades.pnl, 0));
  if (f.dateFrom) conds.push(gte(trades.openedAt, new Date(`${f.dateFrom}T00:00:00Z`)));
  if (f.dateTo) conds.push(lte(trades.openedAt, new Date(`${f.dateTo}T23:59:59.999Z`)));
  return conds.length ? and(...conds) : undefined;
}

/** Paginated, filtered, sorted trades for the main table. */
export async function fetchTradesPage(q: TradeQuery): Promise<{
  rows: TradeRow[];
  total: number;
  page: number;
  pageSize: number;
}> {
  const page = Math.max(1, q.page ?? 1);
  const pageSize = Math.min(200, Math.max(1, q.pageSize ?? 25));
  const where = buildTradeWhere(q);

  const sortCol = SORT_COLUMNS[q.sortKey ?? "openedAt"];
  const orderBy = q.sortDir === "asc" ? asc(sortCol) : desc(sortCol);
  // Stable tie-breaker so pages never overlap (UUIDv7 id is time-ordered).
  const order = [orderBy, desc(trades.id)];

  const [rows, totalRow] = await Promise.all([
    db
      .select()
      .from(trades)
      .where(where)
      .orderBy(...order)
      .limit(pageSize)
      .offset((page - 1) * pageSize),
    db.select({ c: count() }).from(trades).where(where),
  ]);

  return { rows, total: totalRow[0]?.c ?? 0, page, pageSize };
}

/** All closed trades (used for portfolio-wide aggregates and breakdowns). */
export async function fetchClosedTrades(): Promise<TradeRow[]> {
  return db
    .select()
    .from(trades)
    .where(eq(trades.status, "closed"))
    .orderBy(asc(trades.closedAt));
}

/** Currently-open positions (status='open'). */
export async function fetchOpenTrades(): Promise<TradeRow[]> {
  return db
    .select()
    .from(trades)
    .where(eq(trades.status, "open"))
    .orderBy(desc(trades.openedAt));
}

/** Daily equity / metrics rows, oldest first (one point per trading day). */
export async function fetchDailyMetrics() {
  return db.select().from(dailyMetrics).orderBy(asc(dailyMetrics.date));
}

/** Distinct assets, strategies and exit reasons present, for the filter dropdowns. */
export async function fetchFilterOptions(): Promise<{
  assets: string[];
  strategies: string[];
  exitReasons: string[];
}> {
  const [assetRows, strategyRows, exitReasonRows] = await Promise.all([
    db.selectDistinct({ v: trades.asset }).from(trades).orderBy(asc(trades.asset)),
    db
      .selectDistinct({ v: trades.strategy })
      .from(trades)
      .orderBy(asc(trades.strategy)),
    db
      .selectDistinct({ v: trades.exitReason })
      .from(trades)
      .orderBy(asc(trades.exitReason)),
  ]);
  return {
    assets: assetRows.map((r) => r.v).filter(Boolean) as string[],
    strategies: strategyRows.map((r) => r.v).filter((v): v is string => Boolean(v)),
    exitReasons: exitReasonRows
      .map((r) => r.v)
      .filter((v): v is string => Boolean(v)),
  };
}
