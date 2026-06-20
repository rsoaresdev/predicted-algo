/**
 * Drizzle schema — a READ-ONLY typed mirror of the bot's tables
 * (apps/bot/database/models.py). The bot owns and migrates the real schema;
 * the dashboard never writes, so there are no Drizzle migrations here. This
 * file exists purely for type-safe SELECTs.
 */
import {
  pgTable,
  uuid,
  varchar,
  doublePrecision,
  timestamp,
  date,
  integer,
  index,
} from "drizzle-orm/pg-core";

export const trades = pgTable(
  "trades",
  {
    id: uuid("id").primaryKey(),
    asset: varchar("asset", { length: 16 }).notNull(),
    direction: varchar("direction", { length: 8 }).notNull(), // 'long' | 'short'
    strategy: varchar("strategy", { length: 32 }),
    qty: doublePrecision("qty").notNull(),
    entryPrice: doublePrecision("entry_price").notNull(),
    exitPrice: doublePrecision("exit_price"),
    stopPrice: doublePrecision("stop_price"),
    pnl: doublePrecision("pnl"),
    status: varchar("status", { length: 8 }).notNull(), // 'open' | 'closed'
    exitReason: varchar("exit_reason", { length: 32 }),
    openedAt: timestamp("opened_at", { withTimezone: true }).notNull(),
    closedAt: timestamp("closed_at", { withTimezone: true }),
  },
  (t) => [
    index("ix_trades_asset").on(t.asset),
    index("ix_trades_status").on(t.status),
    index("ix_trades_opened_at").on(t.openedAt),
  ],
);

export const dailyMetrics = pgTable("daily_metrics", {
  id: uuid("id").primaryKey(),
  date: date("date").notNull().unique(), // mode 'string' (YYYY-MM-DD)
  startingEquity: doublePrecision("starting_equity"),
  endingEquity: doublePrecision("ending_equity"),
  realizedPnl: doublePrecision("realized_pnl").notNull(),
  numTrades: integer("num_trades").notNull(),
  wins: integer("wins").notNull(),
  losses: integer("losses").notNull(),
  winRate: doublePrecision("win_rate"),
  returnPct: doublePrecision("return_pct"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull(),
});

export type TradeRow = typeof trades.$inferSelect;
export type DailyMetricRow = typeof dailyMetrics.$inferSelect;
