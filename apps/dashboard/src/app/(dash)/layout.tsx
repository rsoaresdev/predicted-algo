import { Activity, LogOut } from "lucide-react";
import { requireSession } from "@/lib/session";
import { logoutAction } from "@/lib/actions/auth";
import { NavLinks } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <div className="flex size-9 items-center justify-center rounded-xl bg-linear-to-br from-primary to-chart-5 text-primary-foreground shadow-[0_6px_20px_-6px_var(--primary)]">
        <Activity className="size-4.5" />
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight">
            Predicted<span className="text-gradient-primary">Algo</span>
          </div>
          <div className="text-[11px] text-muted-foreground">Trading performance</div>
        </div>
      )}
    </div>
  );
}

export default async function DashLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Defense in depth: middleware already gates, but the layout re-checks.
  const session = await requireSession();

  return (
    <div className="flex min-h-screen w-full">
      <aside className="hidden border-r border-sidebar-border bg-sidebar/60 backdrop-blur-xl md:flex md:w-60 md:flex-col">
        <div className="flex h-16 items-center border-b border-sidebar-border px-3">
          <Brand />
        </div>
        <div className="flex-1 px-3 py-4">
          <div className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
            Navigation
          </div>
          <NavLinks />
        </div>
        <div className="m-3 flex items-center gap-2.5 rounded-xl border border-white/6 bg-white/2 px-3 py-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-linear-to-br from-primary/80 to-chart-5/80 text-xs font-semibold text-primary-foreground uppercase">
            {session.user?.name?.[0] ?? "?"}
          </span>
          <div className="min-w-0 leading-tight">
            <div className="truncate text-xs font-medium text-foreground">
              {session.user?.name}
            </div>
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="live-dot scale-75" />
              Live session
            </div>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-background/70 px-4 backdrop-blur-xl md:px-6">
          <div className="md:hidden">
            <Brand compact />
          </div>
          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
            <form action={logoutAction}>
              <Button variant="ghost" size="sm" type="submit">
                <LogOut className="size-4" />
                <span className="hidden sm:inline">Sign out</span>
              </Button>
            </form>
          </div>
        </header>

        <div className="border-b border-border px-2 py-2 md:hidden">
          <NavLinks orientation="horizontal" />
        </div>

        <main className="mx-auto w-full max-w-350 flex-1 px-4 py-6 md:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
