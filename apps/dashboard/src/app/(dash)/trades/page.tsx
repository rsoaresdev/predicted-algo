import { PageHeader } from "@/components/page-header";
import { TradesTable } from "@/components/trades-table";
import { getTradeFilterOptions } from "@/lib/actions/trades";
import { Card, CardContent } from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default async function TradesPage() {
  const { assets, strategies, exitReasons } = await getTradeFilterOptions();
  return (
    <div className="space-y-6">
      <PageHeader
        title="Trades"
        subtitle="Full round-trip history — filter, sort, and click any row for detail."
      />
      <Card>
        <CardContent className="pt-6">
          <TradesTable
            assets={assets}
            strategies={strategies}
            exitReasons={exitReasons}
          />
        </CardContent>
      </Card>
    </div>
  );
}
