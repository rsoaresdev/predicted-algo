"use client";

export interface TooltipEntry {
  name?: string;
  value?: number | string;
  color?: string;
  fill?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

export interface ChartTooltipProps {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  labelFormatter?: (label: string | number) => string;
  valueFormatter?: (value: number | string, entry: TooltipEntry) => string;
  hideLabel?: boolean;
}

/** Shared premium tooltip used by every chart for visual consistency. */
export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
  valueFormatter,
  hideLabel,
}: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="min-w-36 rounded-lg border border-border bg-popover/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      {!hideLabel && (
        <div className="mb-1.5 font-medium text-foreground">
          {labelFormatter && label !== undefined ? labelFormatter(label) : label}
        </div>
      )}
      <div className="space-y-1">
        {payload.map((entry, i) => (
          <div key={i} className="flex items-center gap-2">
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ background: entry.color ?? entry.fill ?? "var(--primary)" }}
            />
            <span className="text-muted-foreground">{entry.name}</span>
            <span className="tabular ml-auto font-medium text-foreground">
              {valueFormatter && entry.value !== undefined
                ? valueFormatter(entry.value, entry)
                : entry.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
