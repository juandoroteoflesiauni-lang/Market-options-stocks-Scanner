"use client";
import type { Greeks } from "@/types";

interface Props {
  greeks: Greeks;
  iv?: number;
  compact?: boolean;
}

interface Cell {
  symbol: string;
  label: string;
  value: number;
}

export function OptionGreekBox({ greeks, iv, compact = false }: Props) {
  const cells: Cell[] = [
    { symbol: "Δ", label: "Delta", value: greeks.delta },
    { symbol: "Γ", label: "Gamma", value: greeks.gamma },
    { symbol: "Θ", label: "Theta", value: greeks.theta },
    { symbol: "V", label: "Vega", value: greeks.vega },
  ];

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: compact ? 2 : 4,
        padding: compact ? 4 : 8,
        background: "rgba(0,195,255,0.04)",
        border: "1px solid rgba(0,195,255,0.12)",
        borderRadius: "var(--radius-md)",
      }}
    >
      {cells.map((cell) => (
        <div
          key={cell.label}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            padding: compact ? "2px 4px" : "4px 6px",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: compact ? 9 : 10,
              color: "#4A5568",
              letterSpacing: "0.06em",
            }}
          >
            {cell.symbol} {cell.label}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: compact ? 11 : 13,
              fontWeight: 600,
              color: "#00C3FF",
            }}
          >
            {cell.value >= 0 ? "+" : ""}
            {cell.value.toFixed(cell.label === "Gamma" ? 4 : 3)}
          </span>
        </div>
      ))}
      {iv !== undefined && (
        <div
          style={{
            gridColumn: "1 / -1",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            paddingTop: compact ? 2 : 4,
            borderTop: "1px solid rgba(255,255,255,0.06)",
            marginTop: compact ? 2 : 4,
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.06em",
            }}
          >
            IV
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: compact ? 11 : 13,
              fontWeight: 600,
              color: "#FFB800",
            }}
          >
            {(iv * 100).toFixed(1)}%
          </span>
        </div>
      )}
    </div>
  );
}
