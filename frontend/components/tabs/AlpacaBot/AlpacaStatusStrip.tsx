"use client";

import * as React from "react";
import { AlertTriangle, Play, RefreshCw } from "lucide-react";
import { formatCurrency } from "@/utils/format";
import type { AlpacaBotState } from "@/hooks/use-alpaca-bot";
import type { MarketSession } from "@/types/alpaca";

const SESSION_COLOR: Record<MarketSession, string> = {
  OPEN: "#00E676",
  PRE: "#FFB800",
  AFTER: "#FFB800",
  CLOSED: "#FF3D5A",
};

interface Props {
  state: AlpacaBotState;
  session: MarketSession;
  equity: number;
  buyingPower: number;
  isCycling: boolean;
  onRunCycle: (allowLive: boolean) => void;
  onRefresh: () => void;
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

export function AlpacaStatusStrip({
  state,
  session,
  equity,
  buyingPower,
  isCycling,
  onRunCycle,
  onRefresh,
}: Props): React.JSX.Element {
  const [allowLive, setAllowLive] = React.useState(false);
  const modeLabel =
    state.tradingMode === "dry_run"
      ? "DRY-RUN"
      : state.tradingMode.toUpperCase();
  const modeColor = state.isLive
    ? "#FF3D5A"
    : state.dryRun
      ? "#00C3FF"
      : "#FFB800";
  const isPdt = state.balance?.pattern_day_trader ?? false;
  const cycleBlocked = state.isLive && !allowLive;

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
          ALPACA EQUITY BOT
        </span>
        <span
          style={{
            padding: "2px 8px",
            background: state.connected
              ? "rgba(0,230,118,0.12)"
              : "rgba(255,61,90,0.12)",
            border: `1px solid ${state.connected ? "rgba(0,230,118,0.4)" : "rgba(255,61,90,0.4)"}`,
            borderRadius: "var(--radius-pill)",
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: state.connected ? "#00E676" : "#FF3D5A",
            letterSpacing: "0.1em",
          }}
        >
          {state.connected ? "CONNECTED" : "OFFLINE"}
        </span>
        <span
          style={{
            padding: "2px 8px",
            background: "rgba(255,255,255,0.04)",
            border: `1px solid ${modeColor}55`,
            borderRadius: "var(--radius-pill)",
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: modeColor,
            letterSpacing: "0.1em",
          }}
        >
          {modeLabel}
        </span>
      </div>

      <div
        style={{ width: 1, height: 28, background: "rgba(255,255,255,0.08)" }}
      />

      <Metric label="EQUITY" value={formatCurrency(equity)} color="#E8EDF5" />
      <Metric
        label="BUYING PWR"
        value={formatCurrency(buyingPower, true)}
        color="#E8EDF5"
      />
      <Metric
        label="POSITIONS"
        value={String(state.positions.length)}
        color="#E8EDF5"
      />
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
          SESSION
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            fontWeight: 600,
            color: SESSION_COLOR[session],
          }}
        >
          {session}
        </span>
      </div>

      {isPdt && (
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 8px",
            background: "rgba(255,61,90,0.15)",
            border: "1px solid rgba(255,61,90,0.4)",
            borderRadius: "var(--radius-pill)",
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#FF3D5A",
            letterSpacing: "0.1em",
          }}
        >
          <AlertTriangle size={11} /> PDT
        </span>
      )}

      <div style={{ flex: 1 }} />

      {state.isLive && (
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: allowLive ? "#FFB800" : "#8B9AAF",
            cursor: "pointer",
            letterSpacing: "0.08em",
          }}
        >
          <input
            type="checkbox"
            checked={allowLive}
            onChange={(e) => setAllowLive(e.target.checked)}
            style={{ accentColor: "#FFB800" }}
          />
          ALLOW LIVE
        </label>
      )}

      <button
        onClick={onRefresh}
        aria-label="Refresh"
        style={{
          display: "flex",
          alignItems: "center",
          padding: "6px 10px",
          background: "rgba(255,255,255,0.04)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 6,
          color: "#8B9AAF",
          cursor: "pointer",
        }}
      >
        <RefreshCw size={12} />
      </button>

      <button
        onClick={() => onRunCycle(allowLive)}
        disabled={isCycling || !state.connected || cycleBlocked}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 14px",
          background: isCycling ? "#4A5568" : "var(--brand-primary, #00C3FF)",
          color: "#000",
          borderRadius: "var(--radius-pill)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.06em",
          border: "none",
          cursor:
            isCycling || !state.connected || cycleBlocked
              ? "not-allowed"
              : "pointer",
          opacity: !state.connected || cycleBlocked ? 0.5 : 1,
        }}
      >
        <Play size={11} />
        {isCycling ? "RUNNING..." : "RUN CYCLE"}
      </button>
    </div>
  );
}
