import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";

export function KpiCard({
  label,
  value,
  icon,
  sub,
  accent = "default",
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  sub?: ReactNode;
  accent?: "gain" | "loss" | "primary" | "default";
}) {
  const accentLine =
    accent === "gain"
      ? "var(--gain)"
      : accent === "loss"
        ? "var(--loss)"
        : "var(--primary)";

  return (
    <div
      className="surface surface-hover accent-top group relative overflow-hidden p-5"
      style={{ "--accent-line": accentLine } as CSSProperties}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        {icon && (
          <span
            className={cn(
              "flex size-8 shrink-0 items-center justify-center rounded-lg ring-1 transition-transform duration-200 group-hover:scale-110 [&_svg]:size-4",
              accent === "gain" && "bg-gain/12 text-gain ring-gain/20",
              accent === "loss" && "bg-loss/12 text-loss ring-loss/20",
              (accent === "default" || accent === "primary") &&
                "bg-primary/12 text-primary ring-primary/20",
            )}
          >
            {icon}
          </span>
        )}
      </div>
      <div
        className={cn(
          "nums mt-3.5 text-[1.7rem] font-semibold leading-none",
          accent === "gain" && "text-gain",
          accent === "loss" && "text-loss",
        )}
      >
        {value}
      </div>
      {sub != null && (
        <div className="mt-2 text-xs text-muted-foreground">{sub}</div>
      )}
    </div>
  );
}
