"use server";

import { requireSession } from "@/lib/session";
import { getQuotes } from "@/lib/quotes";

/**
 * Session-gated live quotes for the given symbols, used by the open-positions
 * view to mark unrealized P&L. Symbols are sanitised (ticker-shaped only) and
 * capped so the action can't be abused as an open price proxy.
 */
export async function fetchQuotes(
  symbols: string[],
): Promise<Record<string, number | null>> {
  await requireSession();
  const clean = [
    ...new Set(
      (symbols ?? [])
        .filter((s) => typeof s === "string" && /^[A-Z][A-Z.\-]{0,7}$/.test(s)),
    ),
  ].slice(0, 25);
  if (clean.length === 0) return {};
  return getQuotes(clean);
}
