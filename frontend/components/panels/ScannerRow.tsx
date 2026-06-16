"use client";
import { memo } from "react";
import { TickerLogo } from "./TickerLogo";
import { riskBarColor } from "@/utils/colors";

interface ScannerRowData {
  symbol: string;
  expectedWinProb: number;
  rrRatio: number;
  moduleBacktestGrade: string;
  optionsGexDataQualityScore: number;
}

interface Props {
  data: ScannerRowData;
  rank: number;
}

function gradeColor(grade: string): string {
  if (grade.startsWith("A")) return "#00E676";
  if (grade.startsWith("B")) return "#FFB800";
  return "#FF3D5A";
}

export const ScannerRow = memo(function ScannerRow({ data, rank }: Props) {
  const winColor = riskBarColor(data.expectedWinProb, 0.5, 0.6);
  const gexColor = riskBarColor(data.optionsGexDataQualityScore, 0.6, 0.8);
  const gradeC = gradeColor(data.moduleBacktestGrade);
  const gexPct = Math.max(0, Math.min(1, data.optionsGexDataQualityScore)) * 100;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "22px 1fr 52px 52px 40px 88px",
        alignItems: "center",
        gap: 8,
        padding: "8px 10px",
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-md)",
        transition: "background 0.15s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.background = "var(--bg-panel)";
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.04em",
        }}
      >
        #{rank}
      </span>

      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <TickerLogo symbol={data.symbol} size={18} />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            fontWeight: 700,
            color: "#00C3FF",
            letterSpacing: "0.04em",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {data.symbol}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            letterSpacing: "0.08em",
          }}
        >
          WIN
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 600,
            color: winColor,
          }}
        >
          {(data.expectedWinProb * 100).toFixed(0)}%
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            letterSpacing: "0.08em",
          }}
        >
          R:R
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#E8EDF5",
          }}
        >
          {data.rrRatio.toFixed(1)}x
        </span>
      </div>

      <div style={{ display: "flex", justifyContent: "center" }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            fontWeight: 700,
            color: gradeC,
            background: `${gradeC}18`,
            border: `1px solid ${gradeC}44`,
            borderRadius: "var(--radius-pill)",
            padding: "1px 6px",
            letterSpacing: "0.04em",
          }}
        >
          {data.moduleBacktestGrade}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.08em",
            }}
          >
            GEX
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: gexColor,
            }}
          >
            {gexPct.toFixed(0)}
          </span>
        </div>
        <div
          style={{
            width: "100%",
            height: 3,
            background: "var(--bg-hover)",
            borderRadius: "var(--radius-pill)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${gexPct}%`,
              background: gexColor,
              borderRadius: "var(--radius-pill)",
              boxShadow: `0 0 4px ${gexColor}66`,
            }}
          />
        </div>
      </div>
    </div>
  );
});
