"use client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { EngineCategory } from "@/types";
import { categoryColor } from "@/utils/colors";

const CATEGORIES: EngineCategory[] = [
  "ML",
  "STATISTICAL",
  "TECHNICAL",
  "OPTIONS",
  "MACRO",
  "HYBRID",
];
const CAT_LABELS: Record<EngineCategory, string> = {
  ML: "ML",
  STATISTICAL: "Statistical",
  TECHNICAL: "Technical",
  OPTIONS: "Options",
  MACRO: "Macro",
  HYBRID: "Hybrid",
};

// Simulate signal confidence decay per category over time since last update
// Each category has different refresh intervals / decay shapes
const DECAY_SHAPES: Record<EngineCategory, (t: number) => number> = {
  ML: (t) => Math.max(20, 85 - t * 0.8 + Math.sin(t * 0.3) * 3),
  STATISTICAL: (t) => Math.max(25, 80 - t * 0.5 + Math.sin(t * 0.2) * 2),
  TECHNICAL: (t) => Math.max(15, 90 - t * 1.2 + Math.sin(t * 0.5) * 5),
  OPTIONS: (t) => Math.max(20, 88 - t * 1.0 + Math.cos(t * 0.4) * 4),
  MACRO: (t) => Math.max(30, 75 - t * 0.4 + Math.sin(t * 0.15) * 2),
  HYBRID: (t) => Math.max(25, 82 - t * 0.6 + Math.cos(t * 0.35) * 3),
};

function buildDecayData(): Array<Record<string, number>> {
  return Array.from({ length: 61 }, (_, t) => {
    const row: Record<string, number> = { t };
    for (const cat of CATEGORIES) {
      row[cat] = Math.round(DECAY_SHAPES[cat](t) * 10) / 10;
    }
    return row;
  });
}

const data = buildDecayData();

const TOOLTIP_STYLE = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--border-muted)",
  borderRadius: 6,
  padding: "8px 12px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "var(--text-primary)",
};

export function SignalDecayMonitor() {
  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        padding: "10px 4px 6px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div
        style={{
          padding: "0 10px",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-muted)",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        Signal Decay Monitor
      </div>

      <div style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 4, right: 16, bottom: 4, left: 0 }}
          >
            <CartesianGrid
              key="grid"
              stroke="var(--border-subtle)"
              strokeDasharray="3 3"
            />
            <XAxis
              key="x-axis"
              dataKey="t"
              tick={{
                fontFamily: "var(--font-mono)",
                fontSize: 8,
                fill: "var(--text-muted)",
              }}
              tickLine={false}
              axisLine={false}
              label={{
                value: "Minutes since update",
                position: "insideBottom",
                fill: "var(--text-muted)",
                fontSize: 8,
                fontFamily: "var(--font-mono)",
                dy: 8,
              }}
            />
            <YAxis
              key="y-axis"
              domain={[0, 100]}
              tick={{
                fontFamily: "var(--font-mono)",
                fontSize: 8,
                fill: "var(--text-muted)",
              }}
              tickLine={false}
              axisLine={false}
              width={34}
              tickFormatter={(v) => `${v}%`}
            />
            <ReferenceLine
              key="threshold-50"
              id="signal-decay-threshold-50"
              y={50}
              stroke="rgba(255,176,32,0.35)"
              strokeDasharray="3 3"
            />
            <Tooltip
              key="tooltip"
              contentStyle={TOOLTIP_STYLE}
              formatter={(v: unknown, name: unknown) => [
                `${Number(v).toFixed(1)}%`,
                CAT_LABELS[String(name) as EngineCategory] ?? String(name),
              ]}
              labelFormatter={(v) => `t+${v}m`}
            />
            <Legend
              key="legend"
              iconType="line"
              iconSize={10}
              wrapperStyle={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                color: "var(--text-secondary)",
                paddingTop: 4,
              }}
              formatter={(name) => CAT_LABELS[name as EngineCategory] ?? name}
            />
            {CATEGORIES.map((cat) => (
              <Line
                key={cat}
                id={`signal-decay-${cat}`}
                dataKey={cat}
                stroke={categoryColor(cat)}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
                name={cat}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
