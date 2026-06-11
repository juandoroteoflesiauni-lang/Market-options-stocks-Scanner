"use client";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { MetricCard } from "@/components/panels/MetricCard";
import type { EnsembleSummary } from "@/services/mock/engines";
import { getEngines } from "@/services/mock/engines";

function buildDistribution(
  basePrice: number,
): Array<{ bin: string; count: number }> {
  const engines = getEngines();
  const moves = engines.map((e) => e.predictedMove);
  const bins = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5];
  return bins.map((b, i) => {
    const next = bins[i + 1] ?? 6;
    return {
      bin: b >= 0 ? `+${b}%` : `${b}%`,
      count: moves.filter((m) => m >= b && m < next).length,
    };
  });
}

interface Props {
  summary: EnsembleSummary;
  basePrice: number;
  horizon: string;
}

const TOOLTIP_STYLE = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--border-muted)",
  borderRadius: 6,
  padding: "6px 10px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "var(--text-primary)",
};

export function EnsembleHero({ summary, basePrice, horizon }: Props) {
  const dist = buildDistribution(basePrice);
  const rangeLow = basePrice * (1 + summary.ci68Low / 100);
  const rangeHigh = basePrice * (1 + summary.ci68High / 100);

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        padding: "12px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {/* Title row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-muted)",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          Ensemble Consensus · {horizon}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-accent)",
          }}
        >
          {summary.meanConfidence.toFixed(1)}% avg confidence
        </span>
      </div>

      <div
        style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}
      >
        {/* Bull/Neutral/Bear bars */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <BullBearBar label="BULL" pct={summary.bullPct} color="#2FB67C" />
          <BullBearBar
            label="NEUTRAL"
            pct={summary.neutralPct}
            color="#6B6B6B"
          />
          <BullBearBar label="BEAR" pct={summary.bearPct} color="#E04E5C" />
        </div>

        {/* Probability distribution histogram */}
        <div style={{ height: 80 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={dist}
              margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
            >
              <CartesianGrid
                key="grid"
                stroke="var(--border-subtle)"
                strokeDasharray="3 3"
              />
              <XAxis
                key="x-axis"
                dataKey="bin"
                tick={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 8,
                  fill: "var(--text-muted)",
                }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis key="y-axis" hide />
              <Tooltip
                key="tooltip"
                contentStyle={TOOLTIP_STYLE}
                cursor={{ fill: "rgba(255,255,255,0.04)" }}
              />
              <Bar
                key="count"
                id="predictive-distribution-count"
                dataKey="count"
                fill="#FFB020"
                fillOpacity={0.75}
                radius={[2, 2, 0, 0]}
                name="Engines"
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* CI metrics */}
        <div
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}
        >
          <MetricCard
            title="68% CI Low"
            value={`$${rangeLow.toFixed(2)}`}
            accentColor="#E04E5C"
          />
          <MetricCard
            title="68% CI High"
            value={`$${rangeHigh.toFixed(2)}`}
            accentColor="#2FB67C"
          />
          <MetricCard
            title="Mean Move"
            value={`${summary.meanMove >= 0 ? "+" : ""}${summary.meanMove.toFixed(2)}%`}
            accentColor={summary.meanMove >= 0 ? "#2FB67C" : "#E04E5C"}
          />
          <MetricCard title="Engines" value="42" accentColor="#FFB020" />
        </div>
      </div>
    </div>
  );
}

function BullBearBar({
  label,
  pct,
  color,
}: {
  label: string;
  pct: number;
  color: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color,
          width: 50,
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
      <div
        style={{
          flex: 1,
          height: 8,
          background: "var(--bg-hover)",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: color,
            borderRadius: 4,
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color,
          minWidth: 36,
          textAlign: "right",
        }}
      >
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}
