"use client";

import { useState, useTransition } from "react";
import {
  getDistribution,
  type Bucket,
  type DistributionRow,
} from "@/lib/actions/distribution";
import { DistributionChart } from "@/components/charts/distribution-chart";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { fmtPercent, fmtSignedCurrency, pnlClass } from "@/lib/format";

export function DistributionView({
  initial,
  initialBucket = "month",
}: {
  initial: DistributionRow[];
  initialBucket?: Bucket;
}) {
  const [bucket, setBucket] = useState<Bucket>(initialBucket);
  const [rows, setRows] = useState<DistributionRow[]>(initial);
  const [pending, startTransition] = useTransition();

  function change(b: Bucket) {
    if (b === bucket) return;
    setBucket(b);
    startTransition(async () => setRows(await getDistribution(b)));
  }

  return (
    <div className="space-y-6">
      <div className="inline-flex rounded-lg border border-border p-0.5">
        {(["week", "month"] as Bucket[]).map((b) => (
          <Button
            key={b}
            variant={bucket === b ? "secondary" : "ghost"}
            size="sm"
            className="capitalize"
            onClick={() => change(b)}
          >
            {b === "week" ? "Weekly" : "Monthly"}
          </Button>
        ))}
      </div>

      <div className={cn(pending && "opacity-60")}>
        <DistributionChart data={rows} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Period</TableHead>
            <TableHead>Wins</TableHead>
            <TableHead>Losses</TableHead>
            <TableHead>Win rate</TableHead>
            <TableHead>P&amp;L</TableHead>
            <TableHead>Cumulative</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                No closed trades yet.
              </TableCell>
            </TableRow>
          ) : (
            rows.map((r) => (
              <TableRow key={r.period}>
                <TableCell className="font-medium">{r.label}</TableCell>
                <TableCell className="text-gain">{r.wins}</TableCell>
                <TableCell className="text-loss">{r.losses}</TableCell>
                <TableCell>{fmtPercent(r.winRate)}</TableCell>
                <TableCell className={cn("font-medium", pnlClass(r.pnl))}>
                  {fmtSignedCurrency(r.pnl)}
                </TableCell>
                <TableCell className={cn("font-medium", pnlClass(r.cumulativePnl))}>
                  {fmtSignedCurrency(r.cumulativePnl)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
