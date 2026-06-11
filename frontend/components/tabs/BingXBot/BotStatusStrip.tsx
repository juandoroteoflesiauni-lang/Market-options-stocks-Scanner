"use client";
import { useState } from "react";
import { AlertTriangle, Square, Pause } from "lucide-react";
import { BotStatusBadge } from "@/components/panels/BotStatusBadge";
import type { PerfStats } from "@/services/mock/bots";
import { formatCurrency, formatPct } from "@/utils/format";

interface Props {
  name: string;
  stats: PerfStats;
  extra?: React.ReactNode;
}

export function BotStatusStrip({ name, stats, extra }: Props) {
  const [status, setStatus] = useState<"RUNNING" | "PAUSED">("RUNNING");

  const dailyColor = stats.dailyPnL >= 0 ? "#00E676" : "#FF3D5A";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "10px 16px",
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        flexWrap: "wrap",
      }}
    >
      {/* Bot name + status */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 600,
            color: "#00C3FF",
            letterSpacing: "0.06em",
          }}
        >
          {name}
        </span>
        <BotStatusBadge status={status} />
      </div>

      <div
        style={{ width: 1, height: 28, background: "rgba(255,255,255,0.08)" }}
      />

      {/* Metrics */}
      <Metric
        label="EQUITY"
        value={formatCurrency(stats.equity)}
        color="#E8EDF5"
      />
      <Metric
        label="DAILY P&L"
        value={`${formatCurrency(Math.abs(stats.dailyPnL))} (${formatPct(stats.dailyPnLPct)})`}
        color={dailyColor}
      />
      <Metric
        label="TOTAL P&L"
        value={formatCurrency(stats.totalPnL)}
        color="#00E676"
      />
      <Metric label="WIN RATE" value={`${stats.winRate}%`} color="#E8EDF5" />
      <Metric
        label="POSITIONS"
        value={String(stats.activePositions)}
        color="#E8EDF5"
      />

      {extra}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Control buttons */}
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        <button
          onClick={() =>
            setStatus((s) => (s === "RUNNING" ? "PAUSED" : "RUNNING"))
          }
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            padding: "5px 12px",
            background: "rgba(255,184,0,0.1)",
            border: "1px solid rgba(255,184,0,0.4)",
            borderRadius: 6,
            color: "#FFB800",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.08em",
          }}
        >
          <Pause size={11} />{" "}
          {status === "RUNNING" ? "PAUSE BOT" : "RESUME BOT"}
        </button>
        <button
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            padding: "5px 12px",
            background: "rgba(255,61,90,0.1)",
            border: "1px solid rgba(255,61,90,0.4)",
            borderRadius: 6,
            color: "#FF3D5A",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.08em",
          }}
        >
          <Square size={11} /> EMERGENCY STOP
        </button>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        flexShrink: 0,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
          letterSpacing: "0.1em",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 13,
          fontWeight: 600,
          color,
        }}
      >
        {value}
      </span>
    </div>
  );
}
