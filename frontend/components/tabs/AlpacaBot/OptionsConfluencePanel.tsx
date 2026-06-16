"use client";

import * as React from "react";
import { Layers } from "lucide-react";
import type {
  AlpacaCandidateAnalysis,
  AlpacaDecision,
  OptionsConfluence,
  OptionsDirection,
} from "@/types/alpaca";

const DIRECTION_COLOR: Record<OptionsDirection, string> = {
  BULL: "#00E676",
  BEAR: "#FF3D5A",
  NEUTRAL: "#8B9AAF",
};

const FAMILY_LABELS: Record<string, string> = {
  momentum: "MOMENTUM",
  volume: "VOLUMEN / FLUJO",
  structure: "ESTRUCTURA / GEX",
};

const ENGINE_LABELS: Record<string, string> = {
  delta_rsi: "Delta-RSI",
  shadow_macd: "Shadow-MACD",
  vidya_iv_gamma: "VIDYA",
  cvd_ndde_gamma: "CVD",
  volume_profile_oi: "Vol-Profile-OI",
  bb_gex: "BB-GEX",
  sma_gamma: "SMA-Gamma",
  hybrid_ribbon: "Hybrid-Ribbon",
};

interface Props {
  analysis: AlpacaCandidateAnalysis | null;
  decision: AlpacaDecision | undefined;
  route1Symbols: string[];
}

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function ScoreBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}): React.JSX.Element {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#8B9AAF",
        }}
      >
        <span>{label}</span>
        <span style={{ color }}>{pct(value)}</span>
      </div>
      <div
        style={{
          height: 4,
          borderRadius: 2,
          background: "rgba(255,255,255,0.06)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${Math.min(100, Math.max(0, value * 100))}%`,
            height: "100%",
            background: color,
            borderRadius: 2,
          }}
        />
      </div>
    </div>
  );
}

function ConfluenceBody({
  confluence,
  decision,
}: {
  confluence: OptionsConfluence;
  decision: AlpacaDecision | undefined;
}): React.JSX.Element {
  const accent = DIRECTION_COLOR[confluence.dominant_direction];
  const engines = Object.entries(confluence.by_engine);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 28,
            fontWeight: 700,
            color: accent,
          }}
        >
          {pct(confluence.score)}
        </span>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: "var(--radius-pill)",
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              fontWeight: 700,
              color: accent,
              background: `${accent}1A`,
              border: `1px solid ${accent}55`,
              width: "fit-content",
            }}
          >
            {confluence.dominant_direction}
          </span>
          {confluence.moderate && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                color: "#FFB800",
              }}
            >
              distribución moderada → size-down
            </span>
          )}
        </div>
        {decision && (
          <span
            style={{
              marginLeft: "auto",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#8B9AAF",
            }}
          >
            blend score {decision.score.toFixed(2)}
          </span>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 8,
        }}
      >
        {Object.entries(confluence.by_family).map(([family, score]) => (
          <ScoreBar
            key={family}
            label={FAMILY_LABELS[family] ?? family.toUpperCase()}
            value={score}
            color={score >= 0.55 ? "#00E676" : score >= 0.35 ? "#FFB800" : "#FF3D5A"}
          />
        ))}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 6,
        }}
      >
        {engines.map(([engine, score]) => (
          <div
            key={engine}
            style={{
              padding: "6px 8px",
              background: "rgba(255,255,255,0.03)",
              borderRadius: "var(--radius-md)",
              border: "1px solid rgba(255,255,255,0.06)",
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 8,
                color: "#4A5568",
                marginBottom: 2,
              }}
            >
              {ENGINE_LABELS[engine] ?? engine}
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: score >= 0.5 ? "#00E676" : "#8B9AAF",
              }}
            >
              {pct(score)}
            </div>
          </div>
        ))}
      </div>

      {confluence.reason_codes.length > 0 && (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            lineHeight: 1.5,
          }}
        >
          {confluence.reason_codes.join(" · ")}
        </div>
      )}
    </div>
  );
}

export function OptionsConfluencePanel({
  analysis,
  decision,
  route1Symbols,
}: Props): React.JSX.Element | null {
  if (!analysis) return null;

  const isRoute1 =
    analysis.route === "priority" ||
    route1Symbols.includes(analysis.symbol.toUpperCase());
  if (!isRoute1) return null;

  const confluence = analysis.options_confluence ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.1em",
        }}
      >
        <Layers size={12} />
        R1 · CONFLUENCIA OPCIONES · {analysis.symbol}
      </div>

      {!confluence ? (
        <div
          style={{
            padding: 12,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#4A5568",
            background: "rgba(255,255,255,0.02)",
            borderRadius: "var(--radius-lg)",
            border: "1px dashed rgba(255,255,255,0.08)",
          }}
        >
          Sin snapshot de opciones — passthrough (decisión solo técnica).
        </div>
      ) : (
        <ConfluenceBody confluence={confluence} decision={decision} />
      )}
    </div>
  );
}
