"use client";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  CHALLENGE_PRESETS,
  generateDailyPnL,
  generateRules,
  type ChallengePreset,
} from "@/data/funding";
import { MetricCard } from "@/components/panels/MetricCard";
import { ChallengeParams } from "./ChallengeParams";
import { RiskDashboard } from "./RiskDashboard";
import { PositionSizer } from "./PositionSizer";
import { BestOpportunities } from "./BestOpportunities";
import { formatCurrency, formatPct } from "@/utils/format";

const ACCENT = "#00E676";

export function FundingChallenge() {
  const [preset, setPreset] = useState<ChallengePreset>(CHALLENGE_PRESETS[0]);

  const series = useMemo(() => generateDailyPnL(preset, 14), [preset]);
  const rules = useMemo(() => generateRules(preset, series), [preset, series]);

  const lastDay = series[series.length - 1];
  const cumPnl = lastDay?.cumulativePnl ?? 0;
  const dailyPnl = lastDay?.pnl ?? 0;
  const currentDD = lastDay?.drawdown ?? 0;
  const balance = preset.accountSize + cumPnl;
  const pctToTarget = (cumPnl / preset.profitTarget) * 100;
  const ddUsedPct = (currentDD / preset.maxDrawdown) * 100;
  const dailyUsedPct =
    (Math.abs(dailyPnl < 0 ? dailyPnl : 0) / preset.dailyLossLimit) * 100;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        height: "100%",
      }}
    >
      {/* Header */}
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
            {preset.firm}
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 14,
              color: "#E8EDF5",
            }}
          >
            {preset.name}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              padding: "1px 6px",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: "var(--radius-sm)",
              letterSpacing: "0.1em",
            }}
          >
            {preset.phase}
          </span>
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
          }}
        >
          Day {series.length} of {preset.maxTradingDays}
        </span>
      </div>

      {/* Macro metric cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 10,
        }}
      >
        <MetricCard
          title="Balance"
          value={formatCurrency(balance, true)}
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
          title="Daily P&L vs Limit"
          value={formatCurrency(dailyPnl, true)}
          delta={dailyUsedPct}
          deltaLabel="limit used"
          accentColor={dailyPnl < 0 ? "#FF3D5A" : ACCENT}
        />
        <MetricCard
          title="Drawdown Used"
          value={`${ddUsedPct.toFixed(1)}%`}
          delta={-ddUsedPct}
          deltaLabel={`of $${preset.maxDrawdown.toLocaleString()}`}
          accentColor={
            ddUsedPct > 75 ? "#FF3D5A" : ddUsedPct > 50 ? "#FFB800" : ACCENT
          }
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
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <ChallengeParams
            selected={preset}
            onSelect={setPreset}
            rules={rules}
          />
          <RiskDashboard
            series={series}
            dailyLossLimit={preset.dailyLossLimit}
            maxDrawdown={preset.maxDrawdown}
            profitTarget={preset.profitTarget}
          />
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
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
