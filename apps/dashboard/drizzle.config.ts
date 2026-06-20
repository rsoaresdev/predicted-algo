/**
 * drizzle-kit config — used only for `npm run db:pull`, which introspects the
 * live database and writes a generated schema we can diff against src/db/schema.ts
 * to confirm parity with the bot's tables. The dashboard never runs migrations.
 */
import { config } from "dotenv";
import { defineConfig } from "drizzle-kit";

config({ path: ".env.local" });

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  out: "./drizzle/introspected",
  dbCredentials: {
    url: process.env.DATABASE_URL ?? "",
  },
});
