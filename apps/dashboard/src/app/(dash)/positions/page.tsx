import { getOpenPositions } from "@/lib/actions/positions";
import { PageHeader } from "@/components/page-header";
import { PositionsTable } from "@/components/positions-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default async function PositionsPage() {
  const positions = await getOpenPositions();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Open Positions"
        subtitle={`${positions.length} position${positions.length === 1 ? "" : "s"} currently open — marked live against the latest market price.`}
      />

      <Card>
        <CardHeader>
          <CardTitle>Holdings</CardTitle>
        </CardHeader>
        <CardContent>
          <PositionsTable positions={positions} />
        </CardContent>
      </Card>
    </div>
  );
}
