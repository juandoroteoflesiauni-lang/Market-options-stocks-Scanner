"use client";
import type { RiskMetricsSnapshot } from "@/store/fundingStore";

interface Props {
  riskMetrics: RiskMetricsSnapshot | null;
  accentColor: string;
}

const panelStyle: React.CSSProperties = {
  background: "rgba(15, 23, 42, 0.4)",
  backdropFilter: "blur(16px)",
  border: "1px solid rgba(255, 255, 255, 0.05)",
  borderRadius: "var(--radius-lg)",
  padding: "16px",
  boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.2)",
};

const labelStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  color: "#8B9AAF",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  display: "block",
  marginBottom: 12,
};

export function RiskDashboard({ riskMetrics, accentColor }: Props) {
  if (!riskMetrics) {
    return (
      <div style={panelStyle}>
        <span style={labelStyle}>Performance Analytics</span>
        <div style={{ color: "#4A5568", fontSize: 12, fontFamily: "var(--font-mono)", textAlign: "center", padding: "40px 0" }}>
          No trades yet. Insert a mock trade to see metrics.
        </div>
      </div>
    );
  }

  const statRow = (label: string, value: string | number, highlightColor?: string) => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#8B9AAF" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, color: highlightColor || "#E8EDF5" }}>
        {value}
      </span>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={panelStyle}>
        <span style={labelStyle}>Performance Analytics (N={riskMetrics.sample_size})</span>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
          <div>
            {statRow("Expectancy (R)", riskMetrics.expectancy_r, accentColor)}
            {statRow("Profit Factor", riskMetrics.profit_factor.toFixed(2))}
            {statRow("Sharpe Ratio", riskMetrics.sharpe.toFixed(2))}
            {statRow("Sortino Ratio", riskMetrics.sortino.toFixed(2))}
          </div>
          <div>
            {statRow("Calmar Ratio", riskMetrics.calmar.toFixed(2))}
            {statRow("Ulcer Index", riskMetrics.ulcer.toFixed(2))}
            {statRow("95% VaR (R)", riskMetrics.var95)}
            {statRow("95% CVaR (R)", riskMetrics.cvar95, "#FF3D5A")}
          </div>
        </div>
      </div>
    </div>
  );
}
