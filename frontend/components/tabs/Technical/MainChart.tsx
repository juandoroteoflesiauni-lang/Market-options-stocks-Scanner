"use client";
import {
  ComposedChart,
  Line,
  Area,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { IndicatorState } from "./IndicatorPanel";
import type { OHLCV } from "@/types";

interface ChartPoint {
  t: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  ema20?: number;
  ema50?: number;
  ema200?: number;
  vwap?: number;
  bbUpper?: number;
  bbLower?: number;
  optRsi?: number;
  deltaOsc?: number;
}

function ema(data: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const result: number[] = [];
  let prev = data[0];
  for (const v of data) {
    const cur = v * k + prev * (1 - k);
    result.push(cur);
    prev = cur;
  }
  return result;
}

function buildChartData(
  candles: OHLCV[],
  indicators: IndicatorState,
): ChartPoint[] {
  const closes = candles.map((c) => c.close);
  const e20 = ema(closes, 20);
  const e50 = ema(closes, 50);
  const e200 = ema(closes, 200);

  let cumVolPrice = 0;
  let cumVol = 0;

  // Options RSI — based on IV-weighted momentum (simulated)
  const optRsiBase = closes.map((c, i) => {
    const pct = i === 0 ? 0 : (c - closes[i - 1]) / closes[i - 1];
    return 50 + pct * 400;
  });

  // Delta oscillator — simulated as smoothed directional move
  const deltaOscBase = closes.map((c, i) => {
    const pct = i === 0 ? 0 : (c - closes[i - 1]) / closes[i - 1];
    return pct * 800;
  });

  return candles.map((c, i) => {
    cumVolPrice += c.close * c.volume;
    cumVol += c.volume;
    const vwapVal = cumVol > 0 ? cumVolPrice / cumVol : c.close;
    const bbMid = e20[i];
    const window = closes.slice(Math.max(0, i - 19), i + 1);
    const std = Math.sqrt(
      window.reduce((s, v) => s + (v - bbMid) ** 2, 0) / window.length,
    );

    const pt: ChartPoint = {
      t: c.time,
      close: c.close,
      high: c.high,
      low: c.low,
      volume: c.volume,
    };

    if (indicators.ema20) pt.ema20 = Math.round(e20[i] * 100) / 100;
    if (indicators.ema50) pt.ema50 = Math.round(e50[i] * 100) / 100;
    if (indicators.ema200) pt.ema200 = Math.round(e200[i] * 100) / 100;
    if (indicators.vwap) pt.vwap = Math.round(vwapVal * 100) / 100;
    if (indicators.bb) {
      pt.bbUpper = Math.round((bbMid + 2 * std) * 100) / 100;
      pt.bbLower = Math.round((bbMid - 2 * std) * 100) / 100;
    }
    if (indicators.optRsi)
      pt.optRsi = Math.max(0, Math.min(100, optRsiBase[i]));
    if (indicators.deltaOsc) pt.deltaOsc = deltaOscBase[i];

    return pt;
  });
}

function formatLabel(time: number, count: number): string {
  const d = new Date(time);
  if (count <= 50)
    return d.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
    });
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

interface Props {
  candles: OHLCV[];
  indicators: IndicatorState;
  spot: number;
}

const TOOLTIP_STYLE = {
  background: "var(--bg-elevated)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 6,
  padding: "8px 12px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "#E8EDF5",
};

export function MainChart({ candles, indicators, spot }: Props) {
  const data = buildChartData(candles, indicators);

  const hasOscillator =
    indicators.optRsi ||
    indicators.deltaOsc ||
    indicators.rsi ||
    indicators.macd;
  const chartH = hasOscillator ? "68%" : "100%";
  const oscH = hasOscillator ? "28%" : "0%";

  const prices = data.map((d) => d.close);
  const pMin = Math.min(...prices) * 0.998;
  const pMax = Math.max(...prices) * 1.002;

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        padding: "10px 0 0",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 320,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "0 14px 8px",
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        {indicators.ema20 && <LegendDot color="#00C3FF" label="EMA 20" />}
        {indicators.ema50 && <LegendDot color="#FFB800" label="EMA 50" />}
        {indicators.ema200 && <LegendDot color="#FF3D5A" label="EMA 200" />}
        {indicators.vwap && <LegendDot color="#10B981" label="VWAP" />}
        {indicators.bb && <LegendDot color="#8B5CF6" label="BB" />}
        {indicators.gexBands && <LegendDot color="#EC4899" label="GEX Bands" />}
      </div>

      {/* Main price chart */}
      <div style={{ height: chartH, minHeight: hasOscillator ? 200 : 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
          >
            <CartesianGrid
              key="grid"
              stroke="rgba(255,255,255,0.04)"
              strokeDasharray="3 3"
            />
            <XAxis
              key="x-axis"
              dataKey="t"
              tickFormatter={(v) => formatLabel(v, data.length)}
              tick={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                fill: "#4A5568",
              }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              key="y-axis"
              domain={[pMin, pMax]}
              tick={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                fill: "#4A5568",
              }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => v.toFixed(0)}
              width={48}
            />
            <Tooltip
              key="tooltip"
              contentStyle={TOOLTIP_STYLE}
              labelFormatter={(v) => new Date(Number(v)).toLocaleString()}
              formatter={(v: any, name: any) => [v.toFixed(2), name]}
            />

            {/* Bollinger bands area */}
            {indicators.bb && (
              <Area
                key="bb-upper"
                id="technical-bb-upper"
                dataKey="bbUpper"
                fill="#8B5CF6"
                fillOpacity={0.05}
                stroke="#8B5CF6"
                strokeWidth={1}
                strokeOpacity={0.4}
                dot={false}
              />
            )}
            {indicators.bb && (
              <Area
                key="bb-lower"
                id="technical-bb-lower"
                dataKey="bbLower"
                fill="#8B5CF6"
                fillOpacity={0.05}
                stroke="#8B5CF6"
                strokeWidth={1}
                strokeOpacity={0.4}
                dot={false}
              />
            )}

            {/* EMAs */}
            {indicators.ema20 && (
              <Line
                key="ema20"
                id="technical-ema20"
                dataKey="ema20"
                stroke="#00C3FF"
                strokeWidth={1.5}
                dot={false}
              />
            )}
            {indicators.ema50 && (
              <Line
                key="ema50"
                id="technical-ema50"
                dataKey="ema50"
                stroke="#FFB800"
                strokeWidth={1.5}
                dot={false}
              />
            )}
            {indicators.ema200 && (
              <Line
                key="ema200"
                id="technical-ema200"
                dataKey="ema200"
                stroke="#FF3D5A"
                strokeWidth={1.5}
                dot={false}
              />
            )}
            {indicators.vwap && (
              <Line
                key="vwap"
                id="technical-vwap"
                dataKey="vwap"
                stroke="#10B981"
                strokeWidth={1.5}
                dot={false}
                strokeDasharray="4 2"
              />
            )}

            {/* Price line */}
            <Line
              key="close"
              id="technical-close"
              dataKey="close"
              stroke="#E8EDF5"
              strokeWidth={1.5}
              dot={false}
            />

            {/* Spot reference */}
            <ReferenceLine
              key="spot-ref"
              id="technical-spot-ref"
              y={spot}
              stroke="rgba(255,255,255,0.15)"
              strokeDasharray="3 3"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Oscillator sub-panel */}
      {hasOscillator && (
        <div
          style={{
            height: oscH,
            borderTop: "1px solid rgba(255,255,255,0.06)",
            minHeight: 60,
          }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart
              data={data}
              margin={{ top: 4, right: 12, bottom: 4, left: 0 }}
            >
              <CartesianGrid
                key="grid"
                stroke="rgba(255,255,255,0.03)"
                strokeDasharray="3 3"
              />
              <XAxis key="osc-x-axis" dataKey="t" hide />
              <YAxis
                key="osc-y-axis"
                tick={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  fill: "#4A5568",
                }}
                tickLine={false}
                axisLine={false}
                width={48}
              />
              {indicators.optRsi && (
                <ReferenceLine
                  key="rsi-70"
                  id="technical-rsi-70"
                  y={70}
                  stroke="#FF3D5A44"
                />
              )}
              {indicators.optRsi && (
                <ReferenceLine
                  key="rsi-30"
                  id="technical-rsi-30"
                  y={30}
                  stroke="#00E67644"
                />
              )}
              {indicators.optRsi && (
                <ReferenceLine
                  key="rsi-50"
                  id="technical-rsi-50"
                  y={50}
                  stroke="rgba(255,255,255,0.1)"
                />
              )}
              {indicators.optRsi && (
                <Line
                  key="opt-rsi"
                  id="technical-opt-rsi"
                  dataKey="optRsi"
                  stroke="#FF3D5A"
                  strokeWidth={1.5}
                  dot={false}
                  name="Options RSI"
                />
              )}
              {indicators.deltaOsc && (
                <ReferenceLine
                  key="delta-zero"
                  id="technical-delta-zero"
                  y={0}
                  stroke="rgba(255,255,255,0.2)"
                />
              )}
              {indicators.deltaOsc && (
                <Bar
                  key="delta-osc"
                  id="technical-delta-osc"
                  dataKey="deltaOsc"
                  fill="#00C3FF"
                  fillOpacity={0.5}
                  name="Delta Osc"
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <div
        style={{ width: 8, height: 2, background: color, borderRadius: 1 }}
      />
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
    </div>
  );
}
