/**
 * Seed the LOCAL DEV database from the bot's REAL backtest, so the dashboard
 * shows genuine strategy performance (not synthetic data).
 *
 * Generate the data first, from the bot, then seed:
 *   cd ../bot && python export_backtest.py 1     # writes scripts/backtest_seed.json
 *   cd ../dashboard && npm run seed
 *
 * Writes, so it must use a WRITABLE role — set SEED_DATABASE_URL (the postgres
 * owner). It deliberately does NOT reuse DATABASE_URL (the read-only role).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { config } from "dotenv";
import postgres from "postgres";

config({ path: ".env.local" });

const URL =
  process.env.SEED_DATABASE_URL ||
  "postgres://postgres:postgres@localhost:5433/predicted";

const SEED_FILE = resolve("scripts", "backtest_seed.json");

type TradeJSON = {
  asset: string;
  direction: string;
  strategy: string | null;
  qty: number;
  entry_price: number;
  exit_price: number | null;
  stop_price: number | null;
  pnl: number | null;
  status: string;
  exit_reason: string | null;
  opened_at: string;
  closed_at: string | null;
};

type MetricJSON = {
  date: string;
  starting_equity: number;
  ending_equity: number;
  realized_pnl: number;
  num_trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  return_pct: number | null;
};

function load(): {
  trades: (Omit<TradeJSON, "opened_at" | "closed_at"> & {
    opened_at: Date;
    closed_at: Date | null;
  })[];
  metrics: MetricJSON[];
  meta: { years: number; generated_at: string; account: number };
} {
  let raw: string;
  try {
    raw = readFileSync(SEED_FILE, "utf8");
  } catch {
    throw new Error(
      `Missing ${SEED_FILE}.\nGenerate the real backtest data first:\n` +
        `  cd ../bot && python export_backtest.py 1`,
    );
  }
  const data = JSON.parse(raw) as {
    trades: TradeJSON[];
    daily_metrics: MetricJSON[];
    years: number;
    generated_at: string;
    account: number;
  };
  const trades = data.trades.map((t) => ({
    ...t,
    opened_at: new Date(t.opened_at),
    closed_at: t.closed_at ? new Date(t.closed_at) : null,
  }));
  return { trades, metrics: data.daily_metrics, meta: data };
}

async function main() {
  const sql = postgres(URL, { max: 1 });
  try {
    const { trades, metrics, meta } = load();
    const open = trades.filter((t) => t.status === "open").length;
    console.log(
      `Seeding REAL ${meta.years}y backtest (generated ${meta.generated_at}): ` +
        `${trades.length} trades (${open} open), ${metrics.length} daily_metrics ` +
        `into ${URL.replace(/:[^:@]+@/, ":****@")}`,
    );

    await sql`TRUNCATE trades, daily_metrics`;
    for (let i = 0; i < trades.length; i += 500) {
      await sql`INSERT INTO trades ${sql(trades.slice(i, i + 500))}`;
    }
    for (let i = 0; i < metrics.length; i += 500) {
      await sql`INSERT INTO daily_metrics ${sql(metrics.slice(i, i + 500))}`;
    }

    const [{ tc }] = await sql`SELECT count(*)::int AS tc FROM trades`;
    const [{ dc }] = await sql`SELECT count(*)::int AS dc FROM daily_metrics`;
    const [{ oc }] =
      await sql`SELECT count(*)::int AS oc FROM trades WHERE status='open'`;
    const [{ eq }] =
      await sql`SELECT ending_equity AS eq FROM daily_metrics ORDER BY date DESC LIMIT 1`;
    console.log(
      `✓ Seeded ${tc} trades (${oc} open), ${dc} daily_metrics rows. ` +
        `Latest equity: $${Number(eq).toFixed(0)} (from $${meta.account}).`,
    );
  } finally {
    await sql.end();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
