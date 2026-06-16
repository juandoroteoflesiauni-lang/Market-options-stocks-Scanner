"use client";
import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { CHALLENGE_PRESETS, MFFU_BUILDER_PRESET } from "@/data/funding";
import { BuilderCockpit } from "./BuilderCockpit";
import { MetricCard } from "@/components/panels/MetricCard";
import { ChallengeParams } from "./ChallengeParams";
import { RiskDashboard } from "./RiskDashboard";
import { PositionSizer } from "./PositionSizer";
import { BestOpportunities } from "./BestOpportunities";
import { useFundingStore } from "@/store/fundingStore";

export function FundingChallenge() {
  const {
    globalContext,
    riskMetrics,
    builderMetrics,
    isLoading,
    error,
    startPolling,
    stopPolling,
    insertMockTrade,
  } = useFundingStore();

  const [preset, setPreset] = useState(MFFU_BUILDER_PRESET);

  useEffect(() => {
    startPolling(5000);
    return () => stopPolling();
  }, [startPolling, stopPolling]);

  // Dynamic colors based on market regime
  const regime = globalContext?.market_regime || "NEUTRAL";
  const ACCENT = useMemo(() => {
    switch (regime) {
      case "MELTDOWN":
        return "#FF3D5A"; // Red
      case "BEAR":
        return "#FFB800"; // Orange
      case "BULL":
        return "#00E676"; // Green
      case "NEUTRAL":
      default:
        return "#00C3FF"; // Blue
    }
  }, [regime]);

  // Compute mock PnL values for the dashboard based on the trades (using sample_size and expectancy as a proxy)
  const cumPnl = riskMetrics ? riskMetrics.sample_size * parseFloat(riskMetrics.expectancy_r) * 100 : 0;
  const balance = preset.accountSize + cumPnl;
  const pctToTarget = (cumPnl / preset.profitTarget) * 100;
  
  const bufferZoneColor = useMemo(() => {
    if (!riskMetrics) return ACCENT;
    if (riskMetrics.buffer_zone === "RED") return "#FF3D5A";
    if (riskMetrics.buffer_zone === "YELLOW") return "#FFB800";
    return "#00E676";
  }, [riskMetrics, ACCENT]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        height: "100%",
        padding: "8px",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "rgba(15, 23, 42, 0.4)",
          backdropFilter: "blur(12px)",
          border: "1px solid rgba(255, 255, 255, 0.05)",
          borderRadius: "var(--radius-lg)",
          padding: "12px 16px",
          boxShadow: "0 4px 24px -4px rgba(0,0,0,0.2)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: ACCENT,
              padding: "4px 10px",
              border: `1px solid ${ACCENT}50`,
              borderRadius: "var(--radius-sm)",
              background: `${ACCENT}15`,
              letterSpacing: "0.1em",
              boxShadow: `0 0 12px ${ACCENT}20`,
            }}
          >
            REGIME: {regime}
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 15,
              fontWeight: 500,
              color: "#E8EDF5",
            }}
          >
            {preset.name} - Phase {preset.phase}
          </span>
          <button
            onClick={insertMockTrade}
            disabled={isLoading}
            style={{
              background: "rgba(255, 255, 255, 0.05)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
              borderRadius: "var(--radius-md)",
              padding: "4px 12px",
              color: "#E8EDF5",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              cursor: isLoading ? "not-allowed" : "pointer",
              transition: "all 0.2s",
            }}
          >
            {isLoading ? "Inserting..." : "+ Insert Mock Trade"}
          </button>
        </div>
        
        {error && (
          <span style={{ color: "#FF3D5A", fontSize: 11, fontFamily: "var(--font-mono)" }}>
            Error: {error}
          </span>
        )}
      </div>

      {/* Macro metric cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 12,
        }}
      >
        <MetricCard
          title="Account Balance"
          value={`$${balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          delta={(cumPnl / preset.accountSize) * 100}
          deltaLabel="vs start"
          accentColor={ACCENT}
        />
        <MetricCard
          title="Profit to Target"
          value={`${pctToTarget.toFixed(1)}%`}
          delta={cumPnl}
          deltaLabel={`of $${preset.profitTarget.toLocaleString()}`}
          accentColor={ACCENT}
        />
        <MetricCard
          title="Kelly Applied"
          value={riskMetrics ? `${(riskMetrics.kelly_applied * 100).toFixed(2)}%` : "0.00%"}
          delta={riskMetrics?.sharpe ?? 0}
          deltaLabel="Sharpe Ratio"
          accentColor={ACCENT}
        />
        <MetricCard
          title="Burn Rate (BUR)"
          value={riskMetrics ? riskMetrics.bur.toFixed(2) : "0.00"}
          delta={riskMetrics?.risk_of_ruin_pct ?? 0}
          deltaLabel="Risk of Ruin %"
          accentColor={bufferZoneColor}
        />
      </div>

      {/* Main body — two columns */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 14,
          flex: 1,
          minHeight: 0,
          overflow: "auto",
        }}
      >
        {/* Left column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <BuilderCockpit metrics={builderMetrics} accentColor={ACCENT} />
          <ChallengeParams
            selected={preset}
            onSelect={setPreset}
            accentColor={ACCENT}
          />
          <RiskDashboard
            riskMetrics={riskMetrics}
            accentColor={ACCENT}
          />
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <PositionSizer
            accountSize={preset.accountSize}
            accentColor={ACCENT}
          />
          <BestOpportunities />
        </div>
      </div>
    </motion.div>
  );
}
