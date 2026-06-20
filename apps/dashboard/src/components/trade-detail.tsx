"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { format, parseISO } from "date-fns";
import { X } from "lucide-react";
import type { TradeDTO } from "@/lib/actions/trades";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  fmtCurrency,
  fmtDuration,
  fmtRatio,
  fmtSignedCurrency,
  fmtSignedPercent,
  pnlClass,
} from "@/lib/format";

function Stat({
  label,
  value,
  className,
}: {
  label: string;
  value: React.ReactNode;
  className?: string;
}) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className={cn("nums mt-0.5 text-sm font-medium", className)}>{value}</dd>
    </div>
  );
}

/**
 * Visual "trade map": where the exit landed relative to the entry and the
 * protective stop, on a single price axis. The track is inset and the marker
 * labels live in a fixed-height row below it so nothing clips or overlaps.
 */
function PriceLadder({ trade }: { trade: TradeDTO }) {
  const { entryPrice, exitPrice, stopPrice, direction } = trade;
  const pts = [entryPrice, exitPrice, stopPrice].filter(
    (v): v is number => v != null,
  );
  if (pts.length < 2) return null;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const lo = min - span * 0.12;
  const hi = max + span * 0.12;
  // Inset to [6,94]% so markers at the extremes never clip the container edges.
  const pos = (v: number) => 6 + ((v - lo) / (hi - lo)) * 88;

  const up = (trade.pnl ?? 0) >= 0;
  const markers: { v: number; label: string; color: string }[] = [
    { v: entryPrice, label: "Entry", color: "var(--muted-foreground)" },
  ];
  if (stopPrice != null)
    markers.push({ v: stopPrice, label: "Stop", color: "var(--loss)" });
  if (exitPrice != null)
    markers.push({
      v: exitPrice,
      label: "Exit",
      color: up ? "var(--gain)" : "var(--loss)",
    });

  return (
    <div className="rounded-xl border border-border bg-white/[0.02] px-4 pb-4 pt-3">
      <div className="mb-4 flex items-center justify-between text-[11px] uppercase tracking-wider text-muted-foreground">
        <span>Trade map</span>
        <span className="capitalize">{direction}</span>
      </div>
      <div className="relative h-16">
        {/* baseline track */}
        <div className="absolute inset-x-0 top-2 h-px bg-border" />
        {/* entry→exit travel band */}
        {exitPrice != null && (
          <div
            className={cn("absolute top-2 h-px", up ? "bg-gain" : "bg-loss")}
            style={{
              left: `${Math.min(pos(entryPrice), pos(exitPrice))}%`,
              width: `${Math.abs(pos(exitPrice) - pos(entryPrice))}%`,
            }}
          />
        )}
        {markers.map((m) => (
          <div
            key={m.label}
            className="absolute top-0 flex -translate-x-1/2 flex-col items-center"
            style={{ left: `${pos(m.v)}%` }}
          >
            <span
              className="mt-[3px] size-2.5 rounded-full ring-2 ring-background"
              style={{ background: m.color }}
            />
            <span className="mt-2 whitespace-nowrap text-[10px] uppercase tracking-wide text-muted-foreground">
              {m.label}
            </span>
            <span className="nums whitespace-nowrap text-[11px] font-medium">
              {fmtCurrency(m.v)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function TradeDetail({
  trade,
  onClose,
}: {
  trade: TradeDTO | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!trade) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    // Lock background scroll while the modal is open.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [trade, onClose]);

  if (!trade) return null;

  const notional = trade.qty * trade.entryPrice;
  const riskPerShare =
    trade.stopPrice != null ? Math.abs(trade.entryPrice - trade.stopPrice) : null;
  const riskDollars = riskPerShare != null ? riskPerShare * trade.qty : null;
  const returnPct = notional ? (trade.pnl ?? 0) / notional : null;
  const rMultiple =
    trade.pnl != null && riskDollars && riskDollars > 0
      ? trade.pnl / riskDollars
      : null;
  const holding = fmtDuration(trade.openedAt, trade.closedAt ?? undefined);

  if (typeof document === "undefined") return null;

  // Portal to <body> so the overlay escapes the Card's containing block (the
  // `.surface` ancestor has backdrop-blur, which would otherwise trap a
  // position:fixed child inside the table panel instead of the full viewport).
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm duration-200 animate-in fade-in"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        className="surface relative z-10 max-h-[88vh] w-full max-w-lg overflow-y-auto rounded-2xl p-6 duration-200 animate-in fade-in zoom-in-95"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-lg font-semibold tracking-tight">{trade.asset}</span>
              <Badge
                variant={trade.direction === "long" ? "default" : "muted"}
                className="capitalize"
              >
                {trade.direction}
              </Badge>
              <Badge variant={trade.status === "open" ? "default" : "muted"}>
                {trade.status}
              </Badge>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {trade.strategy ?? "—"}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Headline P&L */}
        <div className="mt-6">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {trade.status === "open" ? "Realized P&L (closed legs)" : "Realized P&L"}
          </div>
          <div
            className={cn(
              "nums mt-1 text-3xl font-bold leading-none",
              pnlClass(trade.pnl),
            )}
          >
            {trade.pnl == null ? "—" : fmtSignedCurrency(trade.pnl)}
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            <span
              className={cn(
                "nums rounded-full bg-white/[0.04] px-2 py-0.5",
                pnlClass(trade.pnl),
              )}
            >
              {returnPct == null ? "—" : fmtSignedPercent(returnPct)} on notional
            </span>
            {rMultiple != null && (
              <span
                className={cn(
                  "nums rounded-full bg-white/[0.04] px-2 py-0.5",
                  pnlClass(trade.pnl),
                )}
              >
                {rMultiple > 0 ? "+" : ""}
                {fmtRatio(rMultiple)}R
              </span>
            )}
          </div>
        </div>

        <div className="mt-6">
          <PriceLadder trade={trade} />
        </div>

        {/* Stats */}
        <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-4">
          <Stat label="Quantity" value={trade.qty.toFixed(4)} />
          <Stat label="Notional" value={fmtCurrency(notional)} />
          <Stat label="Entry price" value={fmtCurrency(trade.entryPrice)} />
          <Stat label="Exit price" value={fmtCurrency(trade.exitPrice)} />
          <Stat label="Stop price" value={fmtCurrency(trade.stopPrice)} />
          <Stat
            label="Risk at entry"
            value={riskDollars == null ? "—" : fmtCurrency(riskDollars)}
          />
          <Stat
            label={trade.status === "open" ? "Open for" : "Holding period"}
            value={holding}
          />
          <Stat
            label="R-multiple"
            value={rMultiple == null ? "—" : `${fmtRatio(rMultiple)}R`}
          />
        </dl>

        <div className="mt-6 space-y-3 border-t border-border/60 pt-4 text-sm">
          <div className="flex justify-between gap-3">
            <span className="text-muted-foreground">Exit reason</span>
            <span className="text-right font-medium">{trade.exitReason ?? "—"}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted-foreground">Opened</span>
            <span className="nums text-right">
              {format(parseISO(trade.openedAt), "MMM d, yyyy HH:mm")}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted-foreground">Closed</span>
            <span className="nums text-right">
              {trade.closedAt
                ? format(parseISO(trade.closedAt), "MMM d, yyyy HH:mm")
                : "—"}
            </span>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
