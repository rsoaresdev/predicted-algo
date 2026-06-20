import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

/** Small colored pill showing a directional delta (gain/loss/flat). */
export function TrendPill({
  value,
  direction,
  className,
}: {
  value: string;
  direction: "up" | "down" | "flat";
  className?: string;
}) {
  const Icon =
    direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : Minus;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1",
        direction === "up" && "bg-gain/12 text-gain ring-gain/20",
        direction === "down" && "bg-loss/12 text-loss ring-loss/20",
        direction === "flat" && "bg-muted text-muted-foreground ring-border",
        className,
      )}
    >
      <Icon className="size-3.5" />
      <span className="tabular">{value}</span>
    </span>
  );
}
