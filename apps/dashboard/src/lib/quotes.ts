/**
 * Live(ish) market quotes for open-position marking — server-only.
 *
 * The dashboard intentionally holds no broker credentials, so prices come from a
 * free, keyless public endpoint (Yahoo Finance chart API) rather than Alpaca.
 * Results are cached briefly in-process so rapid client polling fans into at
 * most one upstream request per symbol per TTL. Any failure resolves to `null`
 * for that symbol, and the UI falls back to cost basis — a missing price never
 * throws.
 */
import "server-only";

const TTL_MS = 15_000;

type Cached = { at: number; price: number };
const cache = new Map<string, Cached>();

async function fetchOne(symbol: string): Promise<number | null> {
  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
        symbol,
      )}?interval=1d&range=1d`,
      {
        headers: { "User-Agent": "Mozilla/5.0 (dashboard)" },
        // Never let Next cache this — we manage our own short TTL.
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      },
    );
    if (!res.ok) return null;
    const json = (await res.json()) as {
      chart?: { result?: { meta?: { regularMarketPrice?: number } }[] };
    };
    const price = json?.chart?.result?.[0]?.meta?.regularMarketPrice;
    return typeof price === "number" && Number.isFinite(price) ? price : null;
  } catch {
    return null;
  }
}

/** Current price per symbol (or `null` if unavailable). Cached per symbol. */
export async function getQuotes(
  symbols: string[],
): Promise<Record<string, number | null>> {
  const now = Date.now();
  const out: Record<string, number | null> = {};
  const stale: string[] = [];

  for (const s of symbols) {
    const hit = cache.get(s);
    if (hit && now - hit.at < TTL_MS) out[s] = hit.price;
    else stale.push(s);
  }

  await Promise.all(
    stale.map(async (s) => {
      const price = await fetchOne(s);
      if (price != null) cache.set(s, { at: now, price });
      out[s] = price;
    }),
  );

  return out;
}
