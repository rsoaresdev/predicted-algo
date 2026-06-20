"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { format, parseISO } from "date-fns";
import { fmtPercent } from "@/lib/format";
import type { DrawdownPoint } from "@/lib/actions/overview";
import { ChartTooltip } from "./tooltip";

export function DrawdownChart({ data }: { data: DrawdownPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
        <defs>
          <linearGradient id="ddFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--loss)" stopOpacity={0} />
            <stop offset="100%" stopColor="var(--loss)" stopOpacity={0.35} />
          </linearGradient>
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
          tickFormatter={(v: number) => fmtPercent(v, 0)}
          width={48}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          domain={["auto", 0]}
        />
        <Tooltip
          cursor={{ stroke: "var(--border)" }}
          content={
            <ChartTooltip
              labelFormatter={(l) => format(parseISO(String(l)), "EEE, MMM d, yyyy")}
              valueFormatter={(v) => fmtPercent(Number(v), 2)}
            />
          }
        />
        <Area
          type="monotone"
          dataKey="drawdown"
          name="Drawdown"
          stroke="var(--loss)"
          strokeWidth={2}
          fill="url(#ddFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
