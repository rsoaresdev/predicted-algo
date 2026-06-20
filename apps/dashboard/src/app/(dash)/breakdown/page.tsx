import { getBreakdown, type BreakdownRow } from "@/lib/actions/breakdown";
import { PageHeader } from "@/components/page-header";
import { BreakdownBar } from "@/components/charts/breakdown-bar";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { fmtPercent, fmtRatio, fmtSignedCurrency, pnlClass } from "@/lib/format";

export const dynamic = "force-dynamic";

function Section({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No closed trades yet.</p>
        ) : (
          <>
            <BreakdownBar data={rows.map((r) => ({ key: r.key, pnl: r.pnl }))} />
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Trades</TableHead>
                  <TableHead>Win rate</TableHead>
                  <TableHead>PF</TableHead>
                  <TableHead>P&amp;L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.key}>
                    <TableCell className="font-medium">{r.key}</TableCell>
                    <TableCell className="nums">{r.numTrades}</TableCell>
                    <TableCell className="nums">{fmtPercent(r.winRate)}</TableCell>
                    <TableCell className="nums">{fmtRatio(r.profitFactor)}</TableCell>
                    <TableCell className={cn("nums font-medium", pnlClass(r.pnl))}>
                      {fmtSignedCurrency(r.pnl)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default async function BreakdownPage() {
  const { byStrategy, byAsset } = await getBreakdown();
  return (
    <div className="space-y-6">
      <PageHeader title="Breakdown" subtitle="Aggregated performance by strategy and asset." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="By Strategy" rows={byStrategy} />
        <Section title="By Asset" rows={byAsset} />
      </div>
    </div>
  );
}
