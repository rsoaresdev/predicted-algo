"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { OpenPositionDTO } from "@/lib/actions/positions";
import { fetchQuotes } from "@/lib/actions/quotes";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { fmtCurrency, fmtDuration, fmtSignedCurrency, fmtSignedPercent, pnlClass } from "@/lib/format";

const QUOTE_INTERVAL_MS = 20_000;

function unrealized(p: OpenPositionDTO, price: number): number {
  const sign = p.direction === "long" ? 1 : -1;
  return (price - p.entryPrice) * p.qty * sign;
}

export function PositionsTable({ positions }: { positions: OpenPositionDTO[] }) {
  const [quotes, setQuotes] = useState<Record<string, number | null>>({});
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(positions.length > 0);
  const [now, setNow] = useState(() => Date.now());
  const symbols = useRef([...new Set(positions.map((p) => p.asset))]);

  const refresh = useCallback(async () => {
    if (symbols.current.length === 0) return;
    try {
      const q = await fetchQuotes(symbols.current);
      setQuotes(q);
      setUpdatedAt(Date.now());
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll quotes; tick a 1s clock so "ago" + age columns update live.
  useEffect(() => {
    refresh();
    const q = setInterval(refresh, QUOTE_INTERVAL_MS);
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(q);
      clearInterval(t);
    };
  }, [refresh]);

  const priced = positions
    .map((p) => {
      const price = quotes[p.asset] ?? null;
      return price != null ? unrealized(p, price) : null;
    })
    .filter((v): v is number => v != null);
  const totalUnreal = priced.reduce((a, b) => a + b, 0);
  const haveAnyPrice = priced.length > 0;
  const agoSec = updatedAt ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;

  return (
    <div className="space-y-4">
      {/* Live summary strip */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Unrealized P&amp;L (open)
          </div>
          <div
            className={cn(
              "nums text-2xl font-bold leading-none",
              haveAnyPrice ? pnlClass(totalUnreal) : "text-muted-foreground",
            )}
          >
            {haveAnyPrice ? fmtSignedCurrency(totalUnreal) : "—"}
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {loading ? (
            <RefreshCw className="size-3.5 animate-spin" />
          ) : (
            <span className="live-dot scale-75" />
          )}
          <span>
            {agoSec == null
              ? "fetching prices…"
              : `live · updated ${agoSec}s ago`}
          </span>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-border">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30 hover:bg-muted/30">
              <TableHead>Asset</TableHead>
              <TableHead>Side</TableHead>
              <TableHead>Strategy</TableHead>
              <TableHead>Qty</TableHead>
              <TableHead>Entry</TableHead>
              <TableHead>Current</TableHead>
              <TableHead>Mkt value</TableHead>
              <TableHead>Unrealized P&amp;L</TableHead>
              <TableHead>Stop</TableHead>
              <TableHead>Age</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {positions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="h-24 text-center text-muted-foreground">
                  No open positions.
                </TableCell>
              </TableRow>
            ) : (
              positions.map((p) => {
                const price = quotes[p.asset] ?? null;
                const u = price != null ? unrealized(p, price) : null;
                const uPct = price != null ? u! / p.notional : null;
                const mkt = price != null ? price * p.qty : null;
                return (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">{p.asset}</TableCell>
                    <TableCell>
                      <Badge variant="muted" className="capitalize">
                        {p.direction}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {p.strategy ?? "—"}
                    </TableCell>
                    <TableCell className="nums">{p.qty.toFixed(4)}</TableCell>
                    <TableCell className="nums">{fmtCurrency(p.entryPrice)}</TableCell>
                    <TableCell className="nums">{fmtCurrency(price)}</TableCell>
                    <TableCell className="nums">{fmtCurrency(mkt)}</TableCell>
                    <TableCell className={cn("nums font-medium", u != null && pnlClass(u))}>
                      {u == null ? (
                        "—"
                      ) : (
                        <span>
                          {fmtSignedCurrency(u)}{" "}
                          <span className="text-xs opacity-70">
                            {fmtSignedPercent(uPct)}
                          </span>
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="nums">{fmtCurrency(p.stopPrice)}</TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      {fmtDuration(p.openedAt, undefined, 2)}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      <p className="text-xs text-muted-foreground">
        Current prices via public market data (Yahoo Finance), which can be
        delayed up to ~15 minutes and is unavailable outside market hours — so
        unrealized P&amp;L here is indicative, not a broker statement.
      </p>
    </div>
  );
}
