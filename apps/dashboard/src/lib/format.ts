/**
 * Shared, locale-stable formatters. Used everywhere so currency / percent /
 * gain-loss coloring is identical across every page.
 */
import { intervalToDuration } from "date-fns";

/**
 * Exact, human duration between two instants, e.g. "20 minutes",
 * "1 hour and 30 minutes", "1 day, 3 hours and 32 minutes". Shows the largest
 * `maxParts` non-zero units (default 3) and uses "and" before the final unit.
 * Omit `toISO` to measure up to now (useful for still-open positions).
 */
export function fmtDuration(fromISO: string, toISO?: string, maxParts = 3): string {
  const start = new Date(fromISO);
  const end = toISO ? new Date(toISO) : new Date();
  if (!(end > start)) return "0 minutes";
  const d = intervalToDuration({ start, end });
  const units: [number | undefined, string][] = [
    [d.years, "year"],
    [d.months, "month"],
    [d.days, "day"],
    [d.hours, "hour"],
    [d.minutes, "minute"],
  ];
  let parts = units
    .filter(([n]) => n && n > 0)
    .map(([n, u]) => `${n} ${u}${n === 1 ? "" : "s"}`);
  if (parts.length === 0) {
    const s = d.seconds ?? 0;
    return `${s || 1} second${s === 1 ? "" : "s"}`;
  }
  parts = parts.slice(0, maxParts);
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const USD0 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

export function fmtCurrency(n: number | null | undefined, compact = false): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return compact ? USD0.format(n) : USD.format(n);
}

/** Signed currency, e.g. "+$1,204.10" / "-$330.00". */
export function fmtSignedCurrency(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const s = USD.format(Math.abs(n));
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${s}`;
}

/** `value` is a fraction (0.123 → "12.3%"). Set `alreadyPercent` if it is 12.3. */
export function fmtPercent(
  value: number | null | undefined,
  digits = 1,
  alreadyPercent = false,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const pct = alreadyPercent ? value : value * 100;
  return `${pct.toFixed(digits)}%`;
}

/** Signed percent, e.g. "+12.3%". */
export function fmtSignedPercent(
  value: number | null | undefined,
  digits = 1,
  alreadyPercent = false,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const pct = alreadyPercent ? value : value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)}%`;
}

/** Ratios like Sharpe / profit factor; renders ∞ for Infinity. */
export function fmtRatio(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (!Number.isFinite(n)) return "∞";
  return n.toFixed(digits);
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

/** Tailwind text color by sign — the single source of truth for gain/loss. */
export function pnlClass(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n) || n === 0)
    return "text-muted-foreground";
  return n > 0 ? "text-gain" : "text-loss";
}
