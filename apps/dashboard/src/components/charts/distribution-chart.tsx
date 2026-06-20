"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtInt, fmtSignedCurrency } from "@/lib/format";
import type { DistributionRow } from "@/lib/actions/distribution";
import { ChartTooltip, type TooltipEntry } from "./tooltip";

export function DistributionChart({ data }: { data: DistributionRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={16}
        />
        <YAxis
          yAxisId="left"
          allowDecimals={false}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={36}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tickFormatter={(v: number) => fmtSignedCurrency(v).replace(".00", "")}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={64}
        />
        <Tooltip
          cursor={{ fill: "var(--muted)", opacity: 0.3 }}
          content={
            <ChartTooltip
              valueFormatter={(v, entry: TooltipEntry) =>
                entry.dataKey === "cumulativePnl"
                  ? fmtSignedCurrency(Number(v))
                  : fmtInt(Number(v))
              }
            />
          }
        />
        <Legend
          wrapperStyle={{ fontSize: 12 }}
          formatter={(value) => (
            <span className="text-muted-foreground">{value}</span>
          )}
        />
        <Bar
          yAxisId="left"
          dataKey="wins"
          name="Wins"
          stackId="a"
          fill="var(--gain)"
          radius={[0, 0, 0, 0]}
        />
        <Bar
          yAxisId="left"
          dataKey="losses"
          name="Losses"
          stackId="a"
          fill="var(--loss)"
          radius={[3, 3, 0, 0]}
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="cumulativePnl"
          name="Cumulative P&L"
          stroke="var(--chart-2)"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
