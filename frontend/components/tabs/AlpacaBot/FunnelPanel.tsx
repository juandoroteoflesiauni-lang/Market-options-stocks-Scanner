"use client";

import * as React from "react";
import { Filter } from "lucide-react";
import type { EquityCycleResult } from "@/types/alpaca";

interface Props {
  cycle: EquityCycleResult | null;
}

function Stage({
  label,
  count,
  sub,
  color,
}: {
  label: string;
  count: number;
  sub: string;
  color: string;
}) {
  return (
    <div
      style={{
        flex: 1,
        background: "rgba(255,255,255,0.03)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: "var(--radius-lg)",
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
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
          fontFamily: "var(--font-display)",
          fontSize: 22,
          fontWeight: 700,
          color,
        }}
      >
        {count}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#8B9AAF",
        }}
      >
        {sub}
      </span>
    </div>
  );
}

export function FunnelPanel({ cycle }: Props): React.JSX.Element {
  const universeCount = cycle?.universe.length ?? 0;
  const prefilteredCount = cycle?.prefiltered.length ?? 0;
  const route1Count = cycle?.route1_symbols?.length ?? 0;
  const route2Count = cycle?.route2_symbols?.length ?? 0;
  const analyzed = cycle?.analyses.length ?? 0;
  const allowed =
    cycle?.decisions.filter(
      (d) => d.decision === "ALLOW" || d.decision === "SIZE_DOWN",
    ).length ?? 0;
  const authorized =
    cycle?.risk_decisions.filter((r) => r.authorized).length ?? 0;

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-xl)",
        padding: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.1em",
          marginBottom: 12,
        }}
      >
        <Filter size={12} />
        EMBUDO DUAL-ROUTE · R1 prioritaria + R2 scan dinámico
      </div>
      {(route1Count > 0 || route2Count > 0) && (
        <div
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 10,
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#8B9AAF",
          }}
        >
          <span style={{ color: "#00C3FF" }}>R1: {route1Count} fijos</span>
          <span>·</span>
          <span>R2: {route2Count} scan</span>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Stage
          label="UNIVERSO EXT."
          count={universeCount}
          sub="tickers"
          color="#8B9AAF"
        />
        <Arrow />
        <Stage
          label="PRE-FILTRO"
          count={prefilteredCount}
          sub="top-N seleccionadas"
          color="#00C3FF"
        />
        <Arrow />
        <Stage
          label="ANÁLISIS TA"
          count={analyzed}
          sub="evaluadas"
          color="#E8EDF5"
        />
        <Arrow />
        <Stage
          label="LONG OK"
          count={allowed}
          sub="allow / size-down"
          color="#00E676"
        />
        <Arrow />
        <Stage
          label="AUTORIZADAS"
          count={authorized}
          sub="risk desk"
          color="#00E676"
        />
      </div>
    </div>
  );
}

function Arrow(): React.JSX.Element {
  return (
    <span
      style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "#4A5568" }}
      aria-hidden
    >
      →
    </span>
  );
}
