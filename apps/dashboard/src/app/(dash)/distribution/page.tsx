import { getDistribution } from "@/lib/actions/distribution";
import { PageHeader } from "@/components/page-header";
import { DistributionView } from "@/components/distribution-view";
import { Card, CardContent } from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default async function DistributionPage() {
  const initial = await getDistribution("month");
  return (
    <div className="space-y-6">
      <PageHeader
        title="Win / Loss Distribution"
        subtitle="Wins, losses and cumulative P&L over time."
      />
      <Card>
        <CardContent className="pt-6">
          <DistributionView initial={initial} initialBucket="month" />
        </CardContent>
      </Card>
    </div>
  );
}
