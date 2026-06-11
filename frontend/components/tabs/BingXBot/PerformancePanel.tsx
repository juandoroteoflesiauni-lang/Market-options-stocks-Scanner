"use client";
import { useMemo } from "react";
import { PieChart, Pie, Cell, Tooltip } from "recharts";
import { MetricCard } from "@/components/panels/MetricCard";
import { DataTable, type Column } from "@/components/panels/DataTable";
import type { PerfStats, MockTrade } from "@/services/mock/bots";
import { formatCurrency } from "@/utils/format";

interface Props {
  stats: PerfStats;
  trades: MockTrade[];
}

interface TradeRow extends MockTrade {
  pnlStr: string;
  pnlColor: string;
}

const COLS: Column<TradeRow>[] = [
  { key: "ticker", header: "TICKER", width: 64 },
  {
    key: "direction",
    header: "DIR",
    width: 52,
    render: (r) => (
      <span
        style={{
          color: r.direction === "LONG" ? "#00E676" : "#FF3D5A",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
        }}
      >
        {r.direction}
      </span>
    ),
  },
  {
    key: "entry",
    header: "ENTRY",
    align: "right",
    width: 64,
    render: (r) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
        ${r.entry.toFixed(2)}
      </span>
    ),
  },
  {
    key: "exit",
    header: "EXIT",
    align: "right",
    width: 64,
    render: (r) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
        ${r.exit.toFixed(2)}
      </span>
    ),
  },
  {
    key: "pnlStr",
    header: "P&L",
    align: "right",
    width: 70,
    render: (r) => (
      <span
        style={{
          color: r.pnlColor,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
        }}
      >
        {r.pnlStr}
      </span>
    ),
  },
  { key: "duration", header: "DUR", align: "right" },
  {
    key: "strategy",
    header: "STRATEGY",
    render: (r) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#8B9AAF",
        }}
      >
        {r.strategy}
      </span>
    ),
  },
];

export function PerformancePanel({ stats, trades }: Props) {
  const rows: TradeRow[] = useMemo(
    () =>
      trades.map((t) => ({
        ...t,
        pnlStr: `${t.pnl >= 0 ? "+" : ""}$${Math.abs(t.pnl).toFixed(0)}`,
        pnlColor: t.pnl >= 0 ? "#00E676" : "#FF3D5A",
      })),
    [trades],
  );

  const wins = trades.filter((t) => t.pnl > 0).length;
  const losses = trades.filter((t) => t.pnl < 0).length;
  const be = trades.length - wins - losses;

  const pieData = [
    { name: "Win", value: wins, color: "#00E676" },
    { name: "Loss", value: losses, color: "#FF3D5A" },
    { name: "BE", value: be, color: "#FFB800" },
  ].filter((d) => d.value > 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Metric cards */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <MetricCard title="Sharpe" value={stats.sharpe} accentColor="#00C3FF" />
        <MetricCard
          title="Sortino"
          value={stats.sortino}
          accentColor="#7C3AED"
        />
        <MetricCard
          title="Max DD"
          value={`-${stats.maxDrawdown}%`}
          delta={-stats.maxDrawdown}
          accentColor="#FF3D5A"
        />
        <MetricCard
          title="Prof. Factor"
          value={stats.profitFactor}
          accentColor="#00E676"
        />
      </div>

      {/* Win/Loss Pie */}
      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid rgba(255,255,255,0.06)",
          borderRadius: "var(--radius-lg)",
          padding: "12px",
        }}
      >
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.1em",
            marginBottom: 8,
          }}
        >
          WIN / LOSS DISTRIBUTION
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <PieChart width={90} height={90}>
            <Pie
              data={pieData}
              cx="50%"
              cy="50%"
              innerRadius={24}
              outerRadius={40}
              dataKey="value"
              paddingAngle={2}
            >
              {pieData.map((e, i) => (
                <Cell key={i} fill={e.color} fillOpacity={0.85} />
              ))}
            </Pie>
          </PieChart>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {pieData.map((d) => (
              <div
                key={d.name}
                style={{ display: "flex", alignItems: "center", gap: 6 }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: d.color,
                    display: "inline-block",
                  }}
                />
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    color: "#8B9AAF",
                  }}
                >
                  {d.name} {d.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Trade Log */}
      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid rgba(255,255,255,0.06)",
          borderRadius: "var(--radius-lg)",
          padding: "12px",
        }}
      >
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.1em",
            marginBottom: 8,
          }}
        >
          TRADE LOG (LAST {trades.length})
        </div>
        <DataTable<TradeRow>
          columns={COLS}
          data={rows}
          rowKey={(r, i) => r.id || i}
          maxHeight={280}
        />
      </div>
    </div>
  );
}
