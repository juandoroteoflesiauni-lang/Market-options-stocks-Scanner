"use client";
import { memo, useState } from "react";
import { TrendingUp, TrendingDown, Minus, X } from "lucide-react";
import { EngineDetail } from "./EngineDetail";
import type { PredictiveEngine } from "@/types";
import { categoryColor, signalColor, confidenceColor } from "@/utils/colors";

const CATEGORY_LABEL: Record<string, string> = {
  ML: "ML",
  STATISTICAL: "STAT",
  TECHNICAL: "TECH",
  OPTIONS: "OPT",
  MACRO: "MACRO",
  HYBRID: "HYB",
};

interface CardProps {
  engine: PredictiveEngine;
  onClick: () => void;
}

const EngineCard = memo(function EngineCard({ engine, onClick }: CardProps) {
  const catCol = categoryColor(engine.category);
  const sigCol = signalColor(engine.signal);
  const confCol = confidenceColor(engine.confidence);
  const SigIcon =
    engine.signal === "BULL"
      ? TrendingUp
      : engine.signal === "BEAR"
        ? TrendingDown
        : Minus;

  const statusCol =
    engine.status === "ACTIVE"
      ? "var(--signal-bull)"
      : engine.status === "TRAINING"
        ? "var(--signal-warn)"
        : "var(--signal-bear)";

  return (
    <div
      onClick={onClick}
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-md)",
        padding: "8px 9px 7px",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        transition: "border-color 0.12s ease, background 0.12s ease",
        position: "relative",
        overflow: "hidden",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = `${catCol}55`;
        (e.currentTarget as HTMLElement).style.background =
          "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor =
          "var(--border-subtle)";
        (e.currentTarget as HTMLElement).style.background = "var(--bg-panel)";
      }}
    >
      {/* ── Identity: #id · category · status dot ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 4,
        }}
      >
        <div
          style={{ display: "flex", alignItems: "center", gap: 5, minWidth: 0 }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "var(--text-muted)",
            }}
          >
            #{String(engine.id).padStart(2, "0")}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 8,
              color: catCol,
              background: `${catCol}14`,
              border: `1px solid ${catCol}30`,
              borderRadius: 3,
              padding: "0 4px",
              letterSpacing: "0.05em",
            }}
          >
            {CATEGORY_LABEL[engine.category]}
          </span>
        </div>
        <span
          title={engine.status}
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: statusCol,
            boxShadow: `0 0 4px ${statusCol}`,
            flexShrink: 0,
          }}
        />
      </div>

      {/* ── Name (truncated, secondary) ── */}
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "var(--text-secondary)",
          lineHeight: 1.25,
          minHeight: 22,
          overflow: "hidden",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
        }}
      >
        {engine.name}
      </div>

      {/* ── Output: dominant direction + move (primary readout) ── */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
        <SigIcon size={12} color={sigCol} strokeWidth={2.5} />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            color: sigCol,
            fontWeight: 600,
            lineHeight: 1,
          }}
        >
          {engine.predictedMove >= 0 ? "+" : ""}
          {engine.predictedMove.toFixed(1)}%
        </span>
      </div>

      {/* ── Confidence bar + numeric ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <div
          style={{
            flex: 1,
            height: 3,
            background: "var(--bg-hover)",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${engine.confidence}%`,
              background: confCol,
              borderRadius: 1,
            }}
          />
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: confCol,
            minWidth: 22,
            textAlign: "right",
          }}
        >
          {engine.confidence}%
        </span>
      </div>

      {/* ── Footer: 30d accuracy (secondary metric) ── */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontFamily: "var(--font-mono)",
          fontSize: 8,
          color: "var(--text-muted)",
          letterSpacing: "0.04em",
          borderTop: "1px solid var(--border-subtle)",
          paddingTop: 4,
        }}
      >
        <span>30d ACC</span>
        <span style={{ color: confidenceColor(engine.accuracy30d) }}>
          {engine.accuracy30d}%
        </span>
      </div>
    </div>
  );
});

interface Props {
  engines: PredictiveEngine[];
}

export function EngineGrid({ engines }: Props) {
  const [selected, setSelected] = useState<PredictiveEngine | null>(null);

  return (
    <>
      {/* Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(7, 1fr)",
          gap: 6,
        }}
      >
        {engines.map((e) => (
          <EngineCard key={e.id} engine={e} onClick={() => setSelected(e)} />
        ))}
      </div>

      {/* Detail dialog */}
      {selected && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.65)",
            backdropFilter: "blur(4px)",
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          onClick={() => setSelected(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--bg-panel)",
              border: "1px solid var(--border-muted)",
              borderRadius: "var(--radius-lg)",
              padding: "20px",
              width: 560,
              maxWidth: "90vw",
              maxHeight: "80vh",
              overflowY: "auto",
              position: "relative",
            }}
          >
            {/* Dialog header */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 16,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                  fontWeight: 700,
                  color: "var(--text-primary)",
                }}
              >
                {selected.name}
              </span>
              <button
                onClick={() => setSelected(null)}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--text-muted)",
                  padding: 2,
                }}
              >
                <X size={16} />
              </button>
            </div>

            <EngineDetail engine={selected} />
          </div>
        </div>
      )}
    </>
  );
}
