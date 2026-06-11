// @ts-nocheck
"use client";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  type TooltipProps,
} from "recharts";
import type { DailyPnL } from "@/data/funding";

interface Props {
  series: DailyPnL[];
  dailyLossLimit: number;
  maxDrawdown: number;
  profitTarget: number;
}

function PnLTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload as DailyPnL;
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "var(--radius-md)",
        padding: "8px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}
    >
      <div style={{ color: "#8B9AAF", marginBottom: 4 }}>
        Day {d?.day} · {d?.date}
      </div>
      <div
        style={{ color: (d?.cumulativePnl ?? 0) >= 0 ? "#00E676" : "#FF3D5A" }}
      >
        Cumulative: ${(d?.cumulativePnl ?? 0).toFixed(0)}
      </div>
      <div style={{ color: "#8B9AAF" }}>
        Target: ${(d?.target ?? 0).toFixed(0)}
      </div>
    </div>
  );
}

function DDTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload as DailyPnL;
  const pnl = d?.pnl ?? 0;
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "var(--radius-md)",
        padding: "8px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}
    >
      <div style={{ color: "#8B9AAF", marginBottom: 4 }}>
        Day {d?.day} · {d?.date}
      </div>
      <div style={{ color: pnl >= 0 ? "#00E676" : "#FF3D5A" }}>
        Daily P&amp;L: ${pnl.toFixed(0)}
      </div>
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  background: "var(--bg-panel)",
  border: "1px solid rgba(255,255,255,0.06)",
  borderRadius: "var(--radius-lg)",
  padding: "12px 14px",
};

const labelStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  color: "#4A5568",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  display: "block",
  marginBottom: 10,
};

export function RiskDashboard({ series, dailyLossLimit, profitTarget }: Props) {
  const axisStyle = {
    fill: "#4A5568",
    fontFamily: "var(--font-mono)",
    fontSize: 9,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* P&L Curve */}
      <div style={panelStyle}>
        <span style={labelStyle}>P&L Curve vs Target Trajectory</span>
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart
            data={series}
            margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
          >
            <defs>
              <linearGradient
                key="fc-grad-pnl"
                id="fc-gradPnl"
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop
                  key="fc-pnl-top"
                  offset="5%"
                  stopColor="#00E676"
                  stopOpacity={0.25}
                />
                <stop
                  key="fc-pnl-bot"
                  offset="95%"
                  stopColor="#00E676"
                  stopOpacity={0}
                />
              </linearGradient>
              <linearGradient
                key="fc-grad-tgt"
                id="fc-gradTarget"
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop
                  key="fc-tgt-top"
                  offset="5%"
                  stopColor="#00C3FF"
                  stopOpacity={0.1}
                />
                <stop
                  key="fc-tgt-bot"
                  offset="95%"
                  stopColor="#00C3FF"
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              key="grid"
              strokeDasharray="3 3"
              stroke="rgba(255,255,255,0.04)"
            />
            <XAxis
              key="x"
              dataKey="date"
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              key="y"
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
              width={48}
            />
            <Tooltip key="tip" content={<PnLTooltip />} />
            <ReferenceLine
              key="ref-target"
              y={profitTarget}
              stroke="#00E67644"
              strokeDasharray="4 4"
              label={{
                value: "TARGET",
                fill: "#00E676",
                fontSize: 9,
                fontFamily: "var(--font-mono)",
              }}
            />
            <ReferenceLine
              key="ref-danger"
              y={-dailyLossLimit}
              stroke="#FF3D5A44"
              strokeDasharray="4 4"
              label={{
                value: "DANGER",
                fill: "#FF3D5A",
                fontSize: 9,
                fontFamily: "var(--font-mono)",
              }}
            />
            <Area
              key="area-target"
              type="monotone"
              dataKey="target"
              stroke="#00C3FF44"
              strokeWidth={1}
              strokeDasharray="4 2"
              fill="url(#fc-gradTarget)"
              dot={false}
            />
            <Area
              key="area-pnl"
              type="monotone"
              dataKey="cumulativePnl"
              stroke="#00E676"
              strokeWidth={2}
              fill="url(#fc-gradPnl)"
              dot={false}
              activeDot={{ r: 4, fill: "#00E676" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Daily Waterfall */}
      <div style={panelStyle}>
        <span style={labelStyle}>Daily P&L Waterfall</span>
        <ResponsiveContainer width="100%" height={130}>
          <BarChart
            data={series}
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
              dataKey="date"
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              key="y"
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
              width={48}
            />
            <Tooltip key="tip" content={<DDTooltip />} />
            <ReferenceLine
              key="ref-dl"
              y={-dailyLossLimit}
              stroke="#FF3D5A"
              strokeDasharray="4 4"
              strokeWidth={1}
            />
            <Bar
              key="bar-pnl"
              dataKey="pnl"
              radius={[2, 2, 0, 0]}
              isAnimationActive
              animationDuration={600}
            >
              {series.map((entry, i) => (
                <Cell
                  key={`wf-cell-${i}`}
                  fill={entry.pnl >= 0 ? "#00E676" : "#FF3D5A"}
                  fillOpacity={0.85}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
