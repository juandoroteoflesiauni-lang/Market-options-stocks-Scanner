"use client";
import { ScannerRow } from "@/components/panels/ScannerRow";

export function BestOpportunities() {
  const dummyOpportunities = [
    { symbol: "NVDA", expectedWinProb: 0.65, rrRatio: 2.5, moduleBacktestGrade: "A", optionsGexDataQualityScore: 0.9 },
    { symbol: "AAPL", expectedWinProb: 0.61, rrRatio: 2.1, moduleBacktestGrade: "B+", optionsGexDataQualityScore: 0.85 },
  ];

  return (
    <div
      style={{
        background: "rgba(15, 23, 42, 0.4)",
        backdropFilter: "blur(16px)",
        border: "1px solid rgba(255,255,255,0.05)",
        borderRadius: "var(--radius-lg)",
        padding: "16px",
        boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.2)",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        flex: 1,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          Phase A Scanner (Top Ranked)
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, overflowY: "auto", flex: 1 }}>
        {dummyOpportunities.map((op) => (
          <ScannerRow key={op.symbol} data={op as any} rank={1} />
        ))}
      </div>
    </div>
  );
}
