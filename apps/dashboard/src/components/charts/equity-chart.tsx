"use client";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { format, parseISO, subMonths } from "date-fns";
import { fmtCurrency } from "@/lib/format";
import type { EquityPoint } from "@/lib/actions/overview";
import { cn } from "@/lib/utils";
import { ChartTooltip } from "./tooltip";

const RANGES = [
  { label: "1M", months: 1 },
  { label: "3M", months: 3 },
  { label: "6M", months: 6 },
  { label: "1Y", months: 12 },
  { label: "All", months: 0 },
] as const;

export function EquityChart({ data }: { data: EquityPoint[] }) {
  const [range, setRange] = useState<(typeof RANGES)[number]["label"]>("All");

  const view = useMemo(() => {
    const sel = RANGES.find((r) => r.label === range);
    if (!sel || sel.months === 0 || data.length === 0) return data;
    const last = parseISO(data[data.length - 1].date);
    const cutoff = subMonths(last, sel.months);
    const sliced = data.filter((p) => parseISO(p.date) >= cutoff);
    return sliced.length >= 2 ? sliced : data;
  }, [data, range]);

  const start = view[0]?.equity;
  return (
    <div>
      <div className="mb-3 flex justify-end gap-1">
        {RANGES.map((r) => (
          <button
            key={r.label}
            type="button"
            onClick={() => setRange(r.label)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              range === r.label
                ? "bg-primary/15 text-primary ring-1 ring-primary/20"
                : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={320}>
      <AreaChart data={view} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.45} />
            <stop offset="55%" stopColor="var(--chart-1)" stopOpacity={0.12} />
            <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
          </linearGradient>
          <filter id="equityGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow
              dx="0"
              dy="0"
              stdDeviation="4"
              floodColor="var(--chart-1)"
              floodOpacity="0.5"
            />
          </filter>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(d: string) => format(parseISO(d), "MMM ''yy")}
          minTickGap={48}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tickFormatter={(v: number) => fmtCurrency(v, true)}
          width={64}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          domain={["auto", "auto"]}
        />
        {start != null && (
          <ReferenceLine
            y={start}
            stroke="var(--muted-foreground)"
            strokeDasharray="4 4"
            strokeOpacity={0.4}
          />
        )}
        <Tooltip
          cursor={{ stroke: "var(--primary)", strokeOpacity: 0.4 }}
          content={
            <ChartTooltip
              labelFormatter={(l) => format(parseISO(String(l)), "EEE, MMM d, yyyy")}
              valueFormatter={(v) => fmtCurrency(Number(v))}
            />
          }
        />
        <Area
          type="monotone"
          dataKey="equity"
          name="Equity"
          stroke="var(--chart-1)"
          strokeWidth={2.25}
          fill="url(#equityFill)"
          style={{ filter: "url(#equityGlow)" }}
          activeDot={{
            r: 4,
            fill: "var(--chart-1)",
            stroke: "var(--background)",
            strokeWidth: 2,
          }}
        />
      </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
