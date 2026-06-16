"use client";

import * as React from "react";
import { TickerLogo } from "@/components/panels/TickerLogo";
import { formatCurrency, formatPct, formatPrice } from "@/utils/format";
import type { AlpacaPosition } from "@/types/alpaca";

interface Props {
  positions: AlpacaPosition[];
}

function toNum(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function Th({
  children,
  align = "right",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        textAlign: align,
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        color: "#4A5568",
        letterSpacing: "0.08em",
        padding: "6px 8px",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  color = "#E8EDF5",
  align = "right",
}: {
  children: React.ReactNode;
  color?: string;
  align?: "left" | "right";
}) {
  return (
    <td
      style={{
        textAlign: align,
        padding: "6px 8px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color,
      }}
    >
      {children}
    </td>
  );
}

export function PositionsTable({ positions }: Props): React.JSX.Element {
  if (positions.length === 0) {
    return (
      <div
        style={{
          padding: 16,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "#4A5568",
        }}
      >
        Sin posiciones abiertas.
      </div>
    );
  }

  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <Th align="left">SÍMBOLO</Th>
          <Th>QTY</Th>
          <Th>ENTRADA</Th>
          <Th>ACTUAL</Th>
          <Th>uP&L</Th>
        </tr>
      </thead>
      <tbody>
        {positions.map((pos) => {
          const upnl = toNum(pos.unrealized_pl);
          const upnlPct = toNum(pos.unrealized_plpc) * 100;
          const color = upnl >= 0 ? "#00E676" : "#FF3D5A";
          return (
            <tr
              key={pos.symbol}
              style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}
            >
              <Td align="left">
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <TickerLogo symbol={pos.symbol} size={14} />
                  {pos.symbol}
                </span>
              </Td>
              <Td>{toNum(pos.qty)}</Td>
              <Td>${formatPrice(toNum(pos.avg_entry_price))}</Td>
              <Td>${formatPrice(toNum(pos.current_price))}</Td>
              <Td color={color}>
                {formatCurrency(upnl, true)} ({formatPct(upnlPct)})
              </Td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
