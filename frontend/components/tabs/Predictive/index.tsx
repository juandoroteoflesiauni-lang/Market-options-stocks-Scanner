"use client";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { EnsembleHero } from "./EnsembleHero";
import { EngineGrid } from "./EngineGrid";
import { FanChart } from "./FanChart";
import { AccuracyLeaderboard } from "./AccuracyLeaderboard";
import { SignalDecayMonitor } from "./SignalDecayMonitor";
import {
  getEngines,
  getEnsembleSummary,
  getEngineForecasts,
} from "@/services/mock/engines";

const ACCENT = "#FFB020";

const HORIZONS: Array<{ label: string; bars: number }> = [
  { label: "1H", bars: 12 },
  { label: "4H", bars: 24 },
  { label: "1D", bars: 48 },
  { label: "1W", bars: 120 },
];

const BASE_PRICE = 194.82;

export function Predictive() {
  const [horizonIdx, setHorizonIdx] = useState(0);
  const horizon = HORIZONS[horizonIdx];

  const engines = useMemo(() => getEngines(), []);
  const summary = useMemo(() => getEnsembleSummary(), []);
  const forecasts = useMemo(
    () => getEngineForecasts(horizon.bars, BASE_PRICE),
    [horizonIdx],
  );

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        height: "100%",
        overflowY: "auto",
      }}
    >
      {/* ── Header ────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: ACCENT,
              padding: "2px 8px",
              border: `1px solid ${ACCENT}40`,
              borderRadius: "var(--radius-sm)",
              background: `${ACCENT}10`,
              letterSpacing: "0.1em",
            }}
          >
            08
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 14,
              color: "var(--text-primary)",
            }}
          >
            Predictive Engine Ensemble
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            42 models
          </span>
        </div>

        {/* Horizon selector */}
        <div
          style={{
            display: "flex",
            gap: 2,
            background: "var(--bg-elevated)",
            borderRadius: "var(--radius-sm)",
            padding: 2,
          }}
        >
          {HORIZONS.map((h, i) => (
            <button
              key={h.label}
              onClick={() => setHorizonIdx(i)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color:
                  i === horizonIdx ? "var(--bg-void)" : "var(--text-secondary)",
                background: i === horizonIdx ? ACCENT : "transparent",
                border: "none",
                borderRadius: "var(--radius-sm)",
                padding: "3px 10px",
                cursor: "pointer",
                transition: "background 0.15s, color 0.15s",
                fontWeight: i === horizonIdx ? 700 : 400,
              }}
            >
              {h.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Ensemble Hero ──────────────────────────────────────── */}
      <EnsembleHero
        summary={summary}
        basePrice={BASE_PRICE}
        horizon={horizon.label}
      />

      {/* ── Fan Chart ─────────────────────────────────────────── */}
      <FanChart
        forecasts={forecasts}
        basePrice={BASE_PRICE}
        horizon={horizon.label}
      />

      {/* ── Engine Grid 7×6 ───────────────────────────────────── */}
      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          padding: "10px 12px",
        }}
      >
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-muted)",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            marginBottom: 10,
          }}
        >
          Engine Grid — click any card for details
        </div>
        <EngineGrid engines={engines} />
      </div>

      {/* ── Bottom: leaderboard + decay ───────────────────────── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          paddingBottom: 12,
        }}
      >
        <AccuracyLeaderboard engines={engines} />
        <SignalDecayMonitor />
      </div>
    </motion.div>
  );
}
