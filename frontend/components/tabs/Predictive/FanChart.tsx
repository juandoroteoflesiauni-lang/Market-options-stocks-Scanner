"use client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { EngineForecast } from "@/services/mock/engines";
import { categoryColor } from "@/utils/colors";

interface Props {
  forecasts: EngineForecast[];
  basePrice: number;
  horizon: string;
}

const TOOLTIP_STYLE = {
  background: "var(--bg-elevated)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 6,
  padding: "6px 10px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "#E8EDF5",
};

export function FanChart({ forecasts, basePrice, horizon }: Props) {
  if (forecasts.length === 0) return null;

  const bars = forecasts[0].points.length;

  // Build data: one row per time step, one key per engine
  const rows: Array<Record<string, number>> = Array.from(
    { length: bars },
    (_, t) => {
      const row: Record<string, number> = { t };
      for (const f of forecasts) {
        row[`e${f.engineId}`] = f.points[t].value;
      }
      // median ensemble
      const vals = forecasts
        .map((f) => f.points[t].value)
        .sort((a, b) => a - b);
      row.median = vals[Math.floor(vals.length / 2)];
      return row;
    },
  );

  // Y domain
  const allVals = forecasts.flatMap((f) => f.points.map((p) => p.value));
  const yMin = Math.min(...allVals) * 0.997;
  const yMax = Math.max(...allVals) * 1.003;

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        padding: "10px 4px 4px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div
        style={{
          padding: "0 10px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
          }}
        >
          Fan Chart · {horizon}
        </span>
        <div style={{ display: "flex", gap: 10 }}>
          <LegLi color="#00C3FF" label="Ensemble Median" />
          <LegLi color="rgba(255,255,255,0.12)" label="42 Engines" />
        </div>
      </div>

      <div style={{ height: 180 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={rows}
            margin={{ top: 4, right: 12, bottom: 4, left: 0 }}
          >
            <CartesianGrid
              key="grid"
              stroke="rgba(255,255,255,0.04)"
              strokeDasharray="3 3"
            />
            <XAxis
              key="x-axis"
              dataKey="t"
              tick={{
                fontFamily: "var(--font-mono)",
                fontSize: 8,
                fill: "#4A5568",
              }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              key="y-axis"
              domain={[yMin, yMax]}
              tick={{
                fontFamily: "var(--font-mono)",
                fontSize: 8,
                fill: "#4A5568",
              }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => v.toFixed(0)}
              width={48}
            />
            <ReferenceLine
              key="base-ref"
              id="predictive-fan-base-ref"
              y={basePrice}
              stroke="rgba(255,255,255,0.15)"
              strokeDasharray="3 3"
              label={{
                value: "Base",
                fill: "#4A5568",
                fontSize: 8,
                fontFamily: "var(--font-mono)",
              }}
            />
            <Tooltip
              key="tooltip"
              contentStyle={TOOLTIP_STYLE}
              formatter={(v: unknown, name: unknown) => [
                Number(v).toFixed(2),
                String(name) === "median" ? "Median" : String(name),
              ]}
            />

            {/* Individual engine lines — semitransparent, colored by category */}
            {forecasts.map((f) => (
              <Line
                key={f.engineId}
                id={`predictive-fan-engine-${f.engineId}`}
                dataKey={`e${f.engineId}`}
                stroke={categoryColor(
                  f.category as Parameters<typeof categoryColor>[0],
                )}
                strokeWidth={0.6}
                strokeOpacity={0.18}
                dot={false}
                isAnimationActive={false}
              />
            ))}

            {/* Median ensemble line — prominent cyan */}
            <Line
              key="median"
              id="predictive-fan-median"
              dataKey="median"
              stroke="#00C3FF"
              strokeWidth={2}
              dot={false}
              strokeOpacity={0.9}
              name="median"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function LegLi({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <div
        style={{ width: 16, height: 2, background: color, borderRadius: 1 }}
      />
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
        }}
      >
        {label}
      </span>
    </div>
  );
}
