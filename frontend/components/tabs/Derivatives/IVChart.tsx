// @ts-nocheck
"use client";
import { useState, useMemo } from "react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  type TooltipProps,
} from "recharts";
import { generateOptionsChain } from "@/data/unusualActivity";

type IVMode = "smile" | "term" | "gex" | "dex";

interface Props {
  underlyingPrice: number;
}

const EXPIRIES = [
  "Jun-20",
  "Jun-27",
  "Jul-18",
  "Jul-25",
  "Aug-15",
  "Sep-19",
  "Dec-19",
];
const EXPIRY_DAYS = [11, 18, 39, 46, 67, 102, 193];

const MODE_LABELS: Record<IVMode, string> = {
  smile: "IV Smile",
  term: "IV Term Structure",
  gex: "GEX Profile",
  dex: "DEX Profile",
};

function ChartTooltip({
  active,
  payload,
  label,
}: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "var(--radius-md)",
        padding: "6px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
      }}
    >
      <div style={{ color: "#8B9AAF", marginBottom: 3 }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color ?? "#E8EDF5" }}>
          {p.name}: {typeof p.value === "number" ? p.value.toFixed(3) : p.value}
        </div>
      ))}
    </div>
  );
}

export function IVChart({ underlyingPrice }: Props) {
  const [mode, setMode] = useState<IVMode>("smile");

  const smileData = useMemo(() => {
    const chain = generateOptionsChain(underlyingPrice, "Jun-20");
    const atm = Math.round(underlyingPrice / 5) * 5;
    return chain
      .filter((r) => Math.abs(r.strike - atm) <= 50)
      .map((r) => ({
        strike: r.strike,
        callIV: +(r.call.iv * 100).toFixed(2),
        putIV: +(r.put.iv * 100).toFixed(2),
        isATM: r.isATM,
      }));
  }, [underlyingPrice]);

  const termData = useMemo(
    () =>
      EXPIRIES.map((exp, i) => {
        const chain = generateOptionsChain(underlyingPrice, exp);
        const atm = chain.find((r) => r.isATM);
        return {
          expiry: exp,
          days: EXPIRY_DAYS[i],
          callIV: atm ? +(atm.call.iv * 100).toFixed(2) : 25,
          putIV: atm ? +(atm.put.iv * 100).toFixed(2) : 26,
        };
      }),
    [underlyingPrice],
  );

  const gexData = useMemo(() => {
    const chain = generateOptionsChain(underlyingPrice, "Jun-20");
    const atm = Math.round(underlyingPrice / 5) * 5;
    return chain
      .filter((r) => Math.abs(r.strike - atm) <= 50)
      .map((r) => ({
        strike: r.strike,
        gex: +(
          r.call.gamma * r.call.oi * 100 -
          r.put.gamma * r.put.oi * 100
        ).toFixed(2),
      }));
  }, [underlyingPrice]);

  const dexData = useMemo(() => {
    const chain = generateOptionsChain(underlyingPrice, "Jun-20");
    const atm = Math.round(underlyingPrice / 5) * 5;
    return chain
      .filter((r) => Math.abs(r.strike - atm) <= 50)
      .map((r) => ({
        strike: r.strike,
        dex: +(
          (r.call.delta * r.call.oi - Math.abs(r.put.delta) * r.put.oi) /
          1000
        ).toFixed(2),
      }));
  }, [underlyingPrice]);

  const axisStyle = {
    fill: "#4A5568",
    fontFamily: "var(--font-mono)",
    fontSize: 9,
  };

  const renderChart = () => {
    if (mode === "smile") {
      return (
        <LineChart
          data={smileData}
          margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
        >
          <CartesianGrid
            key="grid"
            strokeDasharray="3 3"
            stroke="rgba(255,255,255,0.04)"
          />
          <XAxis
            key="x"
            dataKey="strike"
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            key="y"
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `${v}%`}
            width={42}
          />
          <Tooltip key="tip" content={<ChartTooltip />} />
          <ReferenceLine
            key="ref-atm"
            x={Math.round(underlyingPrice / 5) * 5}
            stroke="#00C3FF44"
            strokeDasharray="3 3"
          />
          <Line
            key="line-call"
            type="monotone"
            dataKey="callIV"
            stroke="#00C3FF"
            strokeWidth={2}
            dot={false}
            name="Call IV"
          />
          <Line
            key="line-put"
            type="monotone"
            dataKey="putIV"
            stroke="#FF3D5A"
            strokeWidth={2}
            dot={false}
            name="Put IV"
          />
        </LineChart>
      );
    }
    if (mode === "term") {
      return (
        <LineChart
          data={termData}
          margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
        >
          <CartesianGrid
            key="grid"
            strokeDasharray="3 3"
            stroke="rgba(255,255,255,0.04)"
          />
          <XAxis
            key="x"
            dataKey="expiry"
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            key="y"
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `${v}%`}
            width={42}
          />
          <Tooltip key="tip" content={<ChartTooltip />} />
          <Line
            key="line-call"
            type="monotone"
            dataKey="callIV"
            stroke="#00C3FF"
            strokeWidth={2}
            dot={{ fill: "#00C3FF", r: 3 }}
            name="Call IV"
          />
          <Line
            key="line-put"
            type="monotone"
            dataKey="putIV"
            stroke="#FF3D5A"
            strokeWidth={2}
            dot={{ fill: "#FF3D5A", r: 3 }}
            name="Put IV"
          />
        </LineChart>
      );
    }
    if (mode === "gex") {
      return (
        <BarChart
          data={gexData}
          margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
        >
          <CartesianGrid
            key="grid"
            strokeDasharray="3 3"
            stroke="rgba(255,255,255,0.04)"
            vertical={false}
          />
          <XAxis
            key="x"
            dataKey="strike"
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            key="y"
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
            width={42}
          />
          <Tooltip key="tip" content={<ChartTooltip />} />
          <ReferenceLine key="ref-zero" y={0} stroke="rgba(255,255,255,0.15)" />
          <Bar
            key="bar-gex"
            dataKey="gex"
            name="GEX"
            fill="#00E676"
            fillOpacity={0.8}
            radius={[2, 2, 0, 0]}
          />
        </BarChart>
      );
    }
    // dex
    return (
      <BarChart
        data={dexData}
        margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
      >
        <CartesianGrid
          key="grid"
          strokeDasharray="3 3"
          stroke="rgba(255,255,255,0.04)"
          vertical={false}
        />
        <XAxis
          key="x"
          dataKey="strike"
          tick={axisStyle}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          key="y"
          tick={axisStyle}
          axisLine={false}
          tickLine={false}
          width={42}
        />
        <Tooltip key="tip" content={<ChartTooltip />} />
        <ReferenceLine key="ref-zero" y={0} stroke="rgba(255,255,255,0.15)" />
        <Bar
          key="bar-dex"
          dataKey="dex"
          name="DEX"
          fill="#FFB800"
          fillOpacity={0.8}
          radius={[2, 2, 0, 0]}
        />
      </BarChart>
    );
  };

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Toggle */}
      <div style={{ display: "flex", gap: 4 }}>
        {(Object.keys(MODE_LABELS) as IVMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              padding: "3px 8px",
              border: "1px solid",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              transition: "all 0.15s ease",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              borderColor: mode === m ? "#FF3D5A" : "rgba(255,255,255,0.08)",
              background: mode === m ? "#FF3D5A15" : "transparent",
              color: mode === m ? "#FF3D5A" : "#4A5568",
            }}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={170}>
        {renderChart()}
      </ResponsiveContainer>
    </div>
  );
}
