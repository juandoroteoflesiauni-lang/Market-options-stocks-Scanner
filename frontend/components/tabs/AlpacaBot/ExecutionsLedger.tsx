"use client";

import * as React from "react";
import { ScrollText } from "lucide-react";
import type { EquityCycleResult } from "@/types/alpaca";

interface Props {
  cycle: EquityCycleResult | null;
}

function asString(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return null;
}

interface ExecRow {
  symbol: string;
  side: string;
  qty: string;
  status: string;
}

function toRow(raw: Record<string, unknown>): ExecRow {
  const intercepted = raw.intercepted === true || raw.reason === "dry_run";
  return {
    symbol: asString(raw.symbol) ?? "—",
    side: (asString(raw.side) ?? "buy").toUpperCase(),
    qty: asString(raw.qty) ?? asString(raw.filled_qty) ?? "—",
    status: intercepted
      ? "DRY-RUN"
      : (asString(raw.status) ?? "submitted").toUpperCase(),
  };
}

export function ExecutionsLedger({ cycle }: Props): React.JSX.Element {
  const rows = React.useMemo<ExecRow[]>(
    () => (cycle?.executions ?? []).map(toRow),
    [cycle],
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.1em",
        }}
      >
        <ScrollText size={12} />
        EJECUCIONES DEL CICLO ({rows.length})
      </div>

      {rows.length === 0 ? (
        <div
          style={{
            padding: 16,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#4A5568",
          }}
        >
          Sin ejecuciones en el último ciclo.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {rows.map((row, idx) => {
            const sideColor = row.side === "SELL" ? "#FF3D5A" : "#00E676";
            const statusColor =
              row.status === "DRY-RUN" ? "#00C3FF" : "#8B9AAF";
            return (
              <div
                key={`${row.symbol}-${idx}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "6px 10px",
                  background: "rgba(255,255,255,0.03)",
                  borderRadius: 6,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                }}
              >
                <span style={{ color: "#E8EDF5", fontWeight: 600 }}>
                  {row.symbol}
                </span>
                <span style={{ color: sideColor }}>{row.side}</span>
                <span style={{ color: "#8B9AAF" }}>{row.qty} sh</span>
                <span style={{ color: statusColor, letterSpacing: "0.08em" }}>
                  {row.status}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
