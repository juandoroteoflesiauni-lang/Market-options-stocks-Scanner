"use client";
import { useMemo } from "react";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { generateOptionsChain } from "@/data/unusualActivity";

interface Props {
  underlyingPrice: number;
}

const panelStyle: React.CSSProperties = {
  background: "var(--bg-elevated)",
  border: "1px solid rgba(255,255,255,0.05)",
  borderRadius: "var(--radius-md)",
  padding: "10px 12px",
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

function PanelLabel({ children }: { children: string }) {
  return (
    <span
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        color: "#4A5568",
        letterSpacing: "0.1em",
        textTransform: "uppercase",
      }}
    >
      {children}
    </span>
  );
}

const axisStyle = {
  fill: "#4A5568",
  fontFamily: "var(--font-mono)",
  fontSize: 8,
};
const chartMargin = { top: 2, right: 4, bottom: 0, left: 0 };

function DeltaGauge({ netDelta }: { netDelta: number }) {
  const clamped = Math.max(-1, Math.min(1, netDelta));
  const angle = clamped * 90; // -90° to +90°
  const r = 44;
  const cx = 64,
    cy = 60;
  const startAngle = Math.PI;
  const endAngle = 0;
  const arcX = (a: number) => cx + r * Math.cos(a);
  const arcY = (a: number) => cy - r * Math.sin(a);

  const bgPath = `M ${arcX(startAngle)} ${arcY(startAngle)} A ${r} ${r} 0 0 1 ${arcX(endAngle)} ${arcY(endAngle)}`;

  const needleRad = Math.PI / 2 - (angle * Math.PI) / 180;
  const needleLen = 38;
  const nx = cx + needleLen * Math.cos(Math.PI - needleRad);
  const ny = cy - needleLen * Math.sin(Math.PI - needleRad);

  const color =
    clamped > 0.2 ? "#00E676" : clamped < -0.2 ? "#FF3D5A" : "#FFB800";

  return (
    <div style={{ ...panelStyle, alignItems: "center" }}>
      <PanelLabel>Delta Net Exposure</PanelLabel>
      <svg width={128} height={72} style={{ overflow: "visible" }}>
        <path
          d={bgPath}
          fill="none"
          stroke="rgba(255,255,255,0.06)"
          strokeWidth={10}
          strokeLinecap="round"
        />
        <path
          d={bgPath}
          fill="none"
          stroke={`${color}30`}
          strokeWidth={8}
          strokeLinecap="round"
        />
        <line
          x1={cx}
          y1={cy}
          x2={nx}
          y2={ny}
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
        />
        <circle cx={cx} cy={cy} r={4} fill={color} />
        <text
          x={cx}
          y={cy + 16}
          textAnchor="middle"
          fontFamily="var(--font-mono)"
          fontSize={12}
          fontWeight={700}
          fill={color}
        >
          {clamped >= 0 ? "+" : ""}
          {(clamped * 100).toFixed(0)}
        </text>
        <text
          x={20}
          y={cy + 2}
          textAnchor="middle"
          fontFamily="var(--font-mono)"
          fontSize={8}
          fill="#FF3D5A"
        >
          BEAR
        </text>
        <text
          x={108}
          y={cy + 2}
          textAnchor="middle"
          fontFamily="var(--font-mono)"
          fontSize={8}
          fill="#00E676"
        >
          BULL
        </text>
      </svg>
    </div>
  );
}

export function GreeksDashboard({ underlyingPrice }: Props) {
  const chain = useMemo(
    () => generateOptionsChain(underlyingPrice, "Jun-20"),
    [underlyingPrice],
  );
  const atm = Math.round(underlyingPrice / 5) * 5;
  const nearby = chain.filter((r) => Math.abs(r.strike - atm) <= 40);

  const netDelta = useMemo(() => {
    const d = nearby.reduce(
      (s, r) => s + r.call.delta * r.call.oi - Math.abs(r.put.delta) * r.put.oi,
      0,
    );
    const total = nearby.reduce((s, r) => s + r.call.oi + r.put.oi, 0) || 1;
    return d / total;
  }, [nearby]);

  const gammaData = nearby.map((r) => ({
    strike: r.strike,
    gamma: +((r.call.gamma * r.call.oi + r.put.gamma * r.put.oi) / 100).toFixed(
      3,
    ),
  }));

  const thetaData = useMemo(() => {
    // # [PD-3][TH][IM] - Use deterministic Math.sin to avoid Math.random impurity in render
    return Array.from({ length: 30 }, (_, i) => ({
      day: i + 1,
      theta: +(-0.05 * Math.exp(-i * 0.05) * (1 + 0.3 * Math.abs(Math.sin(i * 10)))).toFixed(
        4,
      ),
    }));
  }, []);

  const vegaData = nearby.map((r) => ({
    strike: r.strike,
    vega: +(
      (r.call.vega * r.call.oi) / 100 +
      (r.put.vega * r.put.oi) / 100
    ).toFixed(2),
  }));

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        Greeks Dashboard
      </span>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {/* Delta Gauge */}
        <DeltaGauge netDelta={netDelta} />

        {/* Gamma Profile */}
        <div style={panelStyle}>
          <PanelLabel>Gamma Profile</PanelLabel>
          <ResponsiveContainer width="100%" height={80}>
            <BarChart data={gammaData} margin={chartMargin}>
              <XAxis
                key="gx"
                dataKey="strike"
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                key="gy"
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
                width={30}
              />
              <Tooltip
                key="gt"
                contentStyle={{
                  background: "var(--bg-elevated)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 4,
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                }}
                labelStyle={{ color: "#8B9AAF" }}
                itemStyle={{ color: "#FFB800" }}
              />
              <ReferenceLine key="gr" x={atm} stroke="#00C3FF33" />
              <Bar
                key="gb"
                dataKey="gamma"
                fill="#FFB800"
                fillOpacity={0.85}
                radius={[1, 1, 0, 0]}
                name="Γ"
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Theta Decay */}
        <div style={panelStyle}>
          <PanelLabel>Theta Decay Curve</PanelLabel>
          <ResponsiveContainer width="100%" height={80}>
            <LineChart data={thetaData} margin={chartMargin}>
              <XAxis
                key="tx"
                dataKey="day"
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                key="ty"
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
                width={36}
              />
              <Tooltip
                key="tt"
                contentStyle={{
                  background: "var(--bg-elevated)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 4,
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                }}
                labelStyle={{ color: "#8B9AAF" }}
                itemStyle={{ color: "#FF3D5A" }}
              />
              <Line
                key="tl"
                type="monotone"
                dataKey="theta"
                stroke="#FF3D5A"
                strokeWidth={1.5}
                dot={false}
                name="Θ"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Vega Sensitivity */}
        <div style={panelStyle}>
          <PanelLabel>Vega Sensitivity</PanelLabel>
          <ResponsiveContainer width="100%" height={80}>
            <AreaChart data={vegaData} margin={chartMargin}>
              <defs>
                <linearGradient
                  key="drv-grad-vega"
                  id="drv-gradVega"
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    key="drv-vega-top"
                    offset="5%"
                    stopColor="#8B5CF6"
                    stopOpacity={0.3}
                  />
                  <stop
                    key="drv-vega-bot"
                    offset="95%"
                    stopColor="#8B5CF6"
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <XAxis
                key="vx"
                dataKey="strike"
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                key="vy"
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
                width={30}
              />
              <Tooltip
                key="vt"
                contentStyle={{
                  background: "var(--bg-elevated)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 4,
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                }}
                labelStyle={{ color: "#8B9AAF" }}
                itemStyle={{ color: "#8B5CF6" }}
              />
              <Area
                key="area-vega"
                type="monotone"
                dataKey="vega"
                stroke="#8B5CF6"
                strokeWidth={1.5}
                fill="url(#drv-gradVega)"
                dot={false}
                name="V"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
