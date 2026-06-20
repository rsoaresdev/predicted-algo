/**
 * postgres.js + Drizzle client. The connection authenticates as the read-only
 * `dashboard_ro` role (see infra/postgres/01_dashboard_readonly_role.sql), so
 * write attempts are rejected by the database regardless of app code.
 *
 * A single pool is cached on globalThis so Next.js hot-reload / multiple route
 * invocations don't open a new pool each time.
 */
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const url = process.env.DATABASE_URL;
if (!url) {
  throw new Error(
    "DATABASE_URL is not set. Copy .env.example to .env.local and fill it in.",
  );
}

const globalForDb = globalThis as unknown as {
  _pgClient?: ReturnType<typeof postgres>;
};

const client =
  globalForDb._pgClient ??
  postgres(url, {
    max: Number(process.env.DB_POOL_MAX ?? 5),
    idle_timeout: 20,
    connect_timeout: 10,
  });

if (process.env.NODE_ENV !== "production") {
  globalForDb._pgClient = client;
}

export const db = drizzle(client, { schema });
export { schema };
