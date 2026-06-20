import {
  CalendarClock,
  Gauge,
  Layers,
  Percent,
  Scale,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { getOverview } from "@/lib/actions/overview";
import { KpiCard } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { TrendPill } from "@/components/trend-pill";
import { EquityChart } from "@/components/charts/equity-chart";
import { DrawdownChart } from "@/components/charts/drawdown-chart";
import { Sparkline } from "@/components/charts/sparkline";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  fmtCurrency,
  fmtInt,
  fmtPercent,
  fmtRatio,
  fmtSignedCurrency,
  fmtSignedPercent,
} from "@/lib/format";

export const dynamic = "force-dynamic";

function dir(n: number): "up" | "down" | "flat" {
  return n > 0 ? "up" : n < 0 ? "down" : "flat";
}

export default async function OverviewPage() {
  const { kpis: k, equityCurve, drawdownCurve, hasData } = await getOverview();

  if (!hasData) {
    return (
      <div className="space-y-6">
        <PageHeader title="Overview" subtitle="Live account performance" />
        <Card>
          <CardContent className="py-16 text-center text-sm text-muted-foreground">
            No daily metrics yet. Once the bot writes its first{" "}
            <code>daily_metrics</code> row, the equity curve and KPIs appear here.
          </CardContent>
        </Card>
      </div>
    );
  }

  const up = k.totalReturn >= 0;
  const startEquity = equityCurve[0]?.equity ?? k.currentEquity;
  const peakEquity = equityCurve.reduce((m, p) => Math.max(m, p.equity), startEquity);

  return (
    <div className="space-y-6">
      <PageHeader title="Overview" subtitle="Live account performance" />

      {/* Hero — account equity */}
      <section className="surface glow-primary accent-top relative overflow-hidden p-6 duration-500 animate-in fade-in slide-in-from-bottom-2 md:p-8">
        <div className="grid gap-6 md:grid-cols-[1fr_minmax(220px,340px)] md:items-center">
          <div>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <Wallet className="size-4 text-primary" />
                Account Equity
              </div>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-gain/25 bg-gain/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-gain">
                <span className="live-dot" />
                Live
              </span>
            </div>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <span className="text-gradient nums text-[2.75rem] font-bold leading-none md:text-6xl">
                {fmtCurrency(k.currentEquity)}
              </span>
              <TrendPill
                value={fmtSignedPercent(k.totalReturn)}
                direction={dir(k.totalReturn)}
                className="mb-1.5"
              />
            </div>
            <div className="mt-3 text-sm text-muted-foreground">
              <span className={up ? "text-gain" : "text-loss"}>
                {fmtSignedCurrency(k.realizedPnl)}
              </span>{" "}
              realized P&L · current drawdown{" "}
              <span className="text-loss">{fmtPercent(k.currentDrawdown, 1)}</span>
            </div>

            {/* Inline context strip */}
            <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-3 border-t border-border/60 pt-4">
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Starting capital
                </dt>
                <dd className="nums mt-0.5 text-sm font-medium">{fmtCurrency(startEquity)}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Peak equity
                </dt>
                <dd className="nums mt-0.5 text-sm font-medium">{fmtCurrency(peakEquity)}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Sharpe
                </dt>
                <dd className="nums mt-0.5 text-sm font-medium">{fmtRatio(k.sharpe)}</dd>
              </div>
            </dl>
          </div>
          <div className="h-30">
            <Sparkline
              data={equityCurve.map((p) => ({ v: p.equity }))}
              color={up ? "var(--gain)" : "var(--loss)"}
              height={120}
            />
          </div>
        </div>
      </section>

      {/* KPI grid */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="Total Return"
          value={fmtSignedPercent(k.totalReturn)}
          icon={<TrendingUp />}
          accent={up ? "gain" : "loss"}
        />
        <KpiCard label="CAGR" value={fmtSignedPercent(k.cagr)} icon={<CalendarClock />} accent={k.cagr >= 0 ? "gain" : "loss"} />
        <KpiCard label="Sharpe (ann.)" value={fmtRatio(k.sharpe)} icon={<Gauge />} />
        <KpiCard
          label="Max Drawdown"
          value={fmtPercent(k.maxDrawdown, 1)}
          icon={<TrendingDown />}
          accent="loss"
        />
        <KpiCard label="Profit Factor" value={fmtRatio(k.profitFactor)} icon={<Scale />} />
        <KpiCard label="Win Rate" value={fmtPercent(k.winRate)} icon={<Percent />} />
        <KpiCard label="Closed Trades" value={fmtInt(k.numClosedTrades)} icon={<Layers />} />
        <KpiCard
          label="Open Positions"
          value={fmtInt(k.openPositions)}
          icon={<Wallet />}
          accent="primary"
        />
      </div>

      {/* Charts */}
      <Card className="duration-500 animate-in fade-in">
        <CardHeader>
          <CardTitle>Equity Curve</CardTitle>
          <CardDescription>Daily mark-to-market account equity.</CardDescription>
        </CardHeader>
        <CardContent>
          <EquityChart data={equityCurve} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Drawdown</CardTitle>
          <CardDescription>
            Peak-to-trough decline from the running equity high.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DrawdownChart data={drawdownCurve} />
        </CardContent>
      </Card>
    </div>
  );
}
