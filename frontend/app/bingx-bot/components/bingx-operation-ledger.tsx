"use client";

import { BookOpenCheck, Table2 } from "lucide-react";

import {
  EmptyState,
  SourceBadge,
  TerminalPanel,
} from "@/components/ui/terminal";
import type { BingXOperationLedgerRow } from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";

interface BingxOperationLedgerProps {
  operations: BingXOperationLedgerRow[];
}

export function BingxOperationLedger({
  operations,
}: BingxOperationLedgerProps) {
  return (
    <TerminalPanel
      title="Bitacora de operaciones"
      eyebrow={
        <span className="inline-flex items-center gap-1.5">
          <BookOpenCheck className="h-3.5 w-3.5 text-info" />
          Paper learning
        </span>
      }
      source={
        <span className="inline-flex items-center gap-1.5">
          <Table2 className="h-3 w-3 text-brass" />
          {operations.length} registros
        </span>
      }
      actions={<SourceBadge>Audit ledger</SourceBadge>}
    >
      {operations.length === 0 ? (
        <EmptyState
          title="Sin operaciones auditadas"
          description="Cuando el bot ejecute ciclos de paper o live, cada decision y ejecucion quedara registrada aca."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1180px] font-mono text-[11px]">
            <thead>
              <tr className="border-b border-line bg-elevated text-left text-[10px] uppercase tracking-[0.08em] text-ink-600">
                {[
                  "Hora",
                  "Simbolo",
                  "Evento",
                  "Lado",
                  "Decision",
                  "Riesgo",
                  "Ejec.",
                  "Notional",
                  "Qty",
                  "Ref",
                  "P&L",
                  "%",
                  "Motivos",
                  "Modo",
                ].map((cell) => (
                  <th key={cell} className="px-3 py-2 font-bold">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {operations.map((operation) => (
                <OperationRow
                  key={operation.operation_id}
                  operation={operation}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </TerminalPanel>
  );
}

function OperationRow({ operation }: { operation: BingXOperationLedgerRow }) {
  const pnlTone =
    operation.realized_pnl_usdt == null
      ? "text-ink-500"
      : operation.realized_pnl_usdt >= 0
        ? "text-bull"
        : "text-bear";
  const side = operation.side?.toUpperCase() ?? "--";
  const isLong = side.includes("BUY") || side.includes("LONG");
  const reasonText = operation.reason_codes.length
    ? operation.reason_codes.join(" / ")
    : "--";

  return (
    <tr className="border-b border-line transition-colors last:border-0 hover:bg-hover">
      <td className="px-3 py-2 text-ink-600">
        {formatTime(operation.finished_at)}
      </td>
      <td className="px-3 py-2 font-black text-ink-100">{operation.symbol}</td>
      <td className="px-3 py-2">
        <span className="border border-line bg-base px-1.5 py-0.5 text-[10px] font-bold uppercase text-ink-400">
          {operation.event_type}
        </span>
      </td>
      <td
        className={cn(
          "px-3 py-2 font-black uppercase",
          side === "--" ? "text-ink-500" : isLong ? "text-bull" : "text-bear",
        )}
      >
        {side}
      </td>
      <td className="px-3 py-2">
        <span
          className={cn(
            "font-black uppercase",
            operation.suitability === "ALLOW"
              ? "text-bull"
              : operation.suitability === "BLOCK"
                ? "text-bear"
                : "text-ink-400",
          )}
        >
          {operation.suitability ?? "--"}
        </span>
      </td>
      <td className="px-3 py-2">
        <BoolBadge
          value={operation.authorized}
          trueLabel="OK"
          falseLabel="REJECT"
        />
      </td>
      <td className="px-3 py-2">
        <BoolBadge
          value={operation.execution_ok}
          trueLabel="OK"
          falseLabel="FAIL"
        />
      </td>
      <td className="px-3 py-2 tabular-nums text-ink-400">
        {formatUsd(operation.notional_usdt, false)}
      </td>
      <td className="px-3 py-2 tabular-nums text-ink-400">
        {formatNumber(operation.quantity, 6)}
      </td>
      <td className="px-3 py-2 tabular-nums text-ink-400">
        {formatNumber(operation.reference_price, 4)}
      </td>
      <td className={cn("px-3 py-2 font-black tabular-nums", pnlTone)}>
        {formatUsd(operation.realized_pnl_usdt, true)}
      </td>
      <td className={cn("px-3 py-2 font-black tabular-nums", pnlTone)}>
        {formatPct(operation.pnl_pct)}
      </td>
      <td
        className="max-w-[280px] truncate px-3 py-2 text-ink-500"
        title={reasonText}
      >
        {reasonText}
      </td>
      <td className="px-3 py-2">
        <span
          className={cn(
            "border px-1.5 py-0.5 text-[10px] font-bold uppercase",
            operation.dry_run
              ? "border-info/35 bg-info/10 text-info"
              : "border-bear/45 bg-bear/10 text-bear",
          )}
        >
          {operation.dry_run ? "DRY" : "LIVE"}
        </span>
      </td>
    </tr>
  );
}

function BoolBadge({
  value,
  trueLabel,
  falseLabel,
}: {
  value: boolean | null;
  trueLabel: string;
  falseLabel: string;
}) {
  if (value == null) return <span className="text-ink-600">--</span>;
  return (
    <span
      className={cn("font-black uppercase", value ? "text-bull" : "text-bear")}
    >
      {value ? trueLabel : falseLabel}
    </span>
  );
}

function formatTime(value: string | null): string {
  if (!value) return "--";
  try {
    return new Date(value).toISOString().slice(11, 19);
  } catch {
    return "--";
  }
}

function formatNumber(value: number | null, decimals: number): string {
  if (value == null) return "--";
  return value.toFixed(decimals);
}

function formatUsd(value: number | null, signed: boolean): string {
  if (value == null) return "--";
  const sign = signed && value >= 0 ? "+" : "";
  return `${sign}$${value.toFixed(2)}`;
}

function formatPct(value: number | null): string {
  if (value == null) return "--";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}
