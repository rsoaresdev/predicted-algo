"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { format, parseISO } from "date-fns";
import { ArrowDown, ArrowUp, ChevronsUpDown, RotateCcw } from "lucide-react";
import { getTrades, type TradeDTO, type TradesResult } from "@/lib/actions/trades";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { TradeDetail } from "@/components/trade-detail";
import { cn } from "@/lib/utils";
import { fmtCurrency, fmtSignedCurrency, pnlClass } from "@/lib/format";

type SortKey =
  | "openedAt"
  | "closedAt"
  | "asset"
  | "strategy"
  | "direction"
  | "qty"
  | "entryPrice"
  | "exitPrice"
  | "pnl"
  | "status";

const SORTABLE = new Set<SortKey>([
  "openedAt",
  "closedAt",
  "asset",
  "strategy",
  "direction",
  "qty",
  "entryPrice",
  "exitPrice",
  "pnl",
  "status",
]);

const PAGE_SIZE = 25;
const DEFAULTS = {
  asset: "all",
  strategy: "all",
  direction: "all",
  exitReason: "all",
  outcome: "all",
  dateFrom: "",
  dateTo: "",
};

export function TradesTable({
  assets,
  strategies,
  exitReasons,
}: {
  assets: string[];
  strategies: string[];
  exitReasons: string[];
}) {
  const [filters, setFilters] = useState(DEFAULTS);
  const [sortKey, setSortKey] = useState<SortKey>("openedAt");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<TradesResult | null>(null);
  const [selected, setSelected] = useState<TradeDTO | null>(null);
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    startTransition(async () => {
      const res = await getTrades({
        asset: filters.asset,
        strategy: filters.strategy,
        direction: filters.direction,
        exitReason: filters.exitReason,
        outcome: filters.outcome,
        dateFrom: filters.dateFrom || undefined,
        dateTo: filters.dateTo || undefined,
        sortKey,
        sortDir,
        page,
        pageSize: PAGE_SIZE,
      });
      setData(res);
    });
  }, [filters, sortKey, sortDir, page]);

  function setFilter(key: keyof typeof DEFAULTS, value: string) {
    setPage(1);
    setFilters((f) => ({ ...f, [key]: value }));
  }

  function toggleSort(key: SortKey) {
    if (!SORTABLE.has(key)) return;
    setPage(1);
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const columns = useMemo<ColumnDef<TradeDTO>[]>(
    () => [
      {
        accessorKey: "openedAt",
        header: "Opened",
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {format(parseISO(row.original.openedAt), "MMM d, yyyy HH:mm")}
          </span>
        ),
      },
      {
        accessorKey: "asset",
        header: "Asset",
        cell: ({ row }) => <span className="font-medium">{row.original.asset}</span>,
      },
      {
        accessorKey: "direction",
        header: "Side",
        cell: ({ row }) => (
          <Badge variant="muted" className="capitalize">
            {row.original.direction}
          </Badge>
        ),
      },
      {
        accessorKey: "strategy",
        header: "Strategy",
        cell: ({ row }) => (
          <span className="text-muted-foreground">{row.original.strategy ?? "—"}</span>
        ),
      },
      {
        accessorKey: "qty",
        header: "Qty",
        cell: ({ row }) => <span className="nums">{row.original.qty.toFixed(4)}</span>,
      },
      {
        accessorKey: "entryPrice",
        header: "Entry",
        cell: ({ row }) => <span className="nums">{fmtCurrency(row.original.entryPrice)}</span>,
      },
      {
        accessorKey: "exitPrice",
        header: "Exit",
        cell: ({ row }) => <span className="nums">{fmtCurrency(row.original.exitPrice)}</span>,
      },
      {
        accessorKey: "pnl",
        header: "P&L",
        cell: ({ row }) => (
          <span className={cn("nums font-medium", pnlClass(row.original.pnl))}>
            {row.original.pnl == null ? "—" : fmtSignedCurrency(row.original.pnl)}
          </span>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => (
          <Badge variant={row.original.status === "open" ? "default" : "muted"}>
            {row.original.status}
          </Badge>
        ),
      },
      {
        accessorKey: "exitReason",
        header: "Exit reason",
        enableSorting: false,
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {row.original.exitReason ?? "—"}
          </span>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    data: data?.rows ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
  });

  const total = data?.total ?? 0;
  const pageCount = data?.pageCount ?? 1;
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, total);
  const filtersActive = JSON.stringify(filters) !== JSON.stringify(DEFAULTS);

  return (
    <div className="space-y-4">
      {/* Filter toolbar */}
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Asset</label>
          <Select
            className="w-32"
            value={filters.asset}
            onChange={(e) => setFilter("asset", e.target.value)}
          >
            <option value="all">All assets</option>
            {assets.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Strategy</label>
          <Select
            className="w-40"
            value={filters.strategy}
            onChange={(e) => setFilter("strategy", e.target.value)}
          >
            <option value="all">All strategies</option>
            {strategies.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Side</label>
          <Select
            className="w-28"
            value={filters.direction}
            onChange={(e) => setFilter("direction", e.target.value)}
          >
            <option value="all">All</option>
            <option value="long">Long</option>
            <option value="short">Short</option>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Outcome</label>
          <Select
            className="w-32"
            value={filters.outcome}
            onChange={(e) => setFilter("outcome", e.target.value)}
          >
            <option value="all">All</option>
            <option value="win">Wins</option>
            <option value="loss">Losses</option>
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Exit reason</label>
          <Select
            className="w-48"
            value={filters.exitReason}
            onChange={(e) => setFilter("exitReason", e.target.value)}
          >
            <option value="all">All reasons</option>
            {exitReasons.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">From</label>
          <Input
            type="date"
            className="w-40"
            value={filters.dateFrom}
            onChange={(e) => setFilter("dateFrom", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">To</label>
          <Input
            type="date"
            className="w-40"
            value={filters.dateTo}
            onChange={(e) => setFilter("dateTo", e.target.value)}
          />
        </div>
        {filtersActive && (
          <Button variant="ghost" size="sm" onClick={() => { setPage(1); setFilters(DEFAULTS); }}>
            <RotateCcw className="size-3.5" /> Reset
          </Button>
        )}
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id} className="bg-muted/30 hover:bg-muted/30">
                {hg.headers.map((header) => {
                  const key = header.column.id as SortKey;
                  const sortable = SORTABLE.has(key);
                  const isActive = sortKey === key;
                  return (
                    <TableHead key={header.id}>
                      {sortable ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(key)}
                          className="inline-flex items-center gap-1 hover:text-foreground"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {isActive ? (
                            sortDir === "asc" ? (
                              <ArrowUp className="size-3" />
                            ) : (
                              <ArrowDown className="size-3" />
                            )
                          ) : (
                            <ChevronsUpDown className="size-3 opacity-40" />
                          )}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-32 text-center text-muted-foreground">
                  {pending ? "Loading…" : "No trades match these filters."}
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  onClick={() => setSelected(row.original)}
                  className={cn(
                    "cursor-pointer",
                    pending && "opacity-60",
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
        <span>
          {from}–{to} of {total} trades
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1 || pending}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </Button>
          <span className="tabular">
            Page {page} / {pageCount}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= pageCount || pending}
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
          >
            Next
          </Button>
        </div>
      </div>

      <TradeDetail trade={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
