"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { MetricCard } from "@/components/panels/MetricCard";
import { OptionsChain } from "./OptionsChain";
import { IVChart } from "./IVChart";
import { GreeksDashboard } from "./GreeksDashboard";
import { UnusualActivityFeed } from "./UnusualActivityFeed";
import { generateOptionsChain } from "@/data/unusualActivity";

const ACCENT = "#FF3D5A";

const UNDERLYING_PRICE = 211.42;

export function Derivatives() {
  const [strikeSelected, setStrikeSelected] = useState<number | null>(null);

  const chain = generateOptionsChain(UNDERLYING_PRICE, "Jun-20");
  const atm = chain.find((r) => r.isATM);
  const atmIV = atm ? (((atm.call.iv + atm.put.iv) / 2) * 100).toFixed(1) : "—";
  const ivRank = "34.2";
  const ivPct = "41.8";
  const pcRatio = (
    chain.reduce((s, r) => s + r.put.volume, 0) /
    Math.max(
      1,
      chain.reduce((s, r) => s + r.call.volume, 0),
    )
  ).toFixed(2);
  const gex = chain.reduce(
    (s, r) => s + r.call.gamma * r.call.oi - r.put.gamma * r.put.oi,
    0,
  );
  const maxPainStrike = chain.reduce(
    (best, row) => {
      const pain = chain.reduce((s, r) => {
        return (
          s +
          Math.max(0, row.strike - r.strike) * r.call.oi +
          Math.max(0, r.strike - row.strike) * r.put.oi
        );
      }, 0);
      return pain < best.pain ? { strike: row.strike, pain } : best;
    },
    { strike: 0, pain: Infinity },
  ).strike;

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
            AAPL
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 14,
              color: "#E8EDF5",
            }}
          >
            Options Analytics Terminal
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              fontWeight: 700,
              color: "#E8EDF5",
            }}
          >
            ${UNDERLYING_PRICE.toFixed(2)}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#00E676",
            }}
          >
            +1.24%
          </span>
        </div>
      </div>

      {/* Metric row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(6, 1fr)",
          gap: 10,
        }}
      >
        <MetricCard title="ATM IV" value={`${atmIV}%`} accentColor={ACCENT} />
        <MetricCard
          title="IV Rank"
          value={`${ivRank}`}
          unit="%"
          accentColor="#FFB800"
        />
        <MetricCard
          title="IV Percentile"
          value={`${ivPct}`}
          unit="%"
          accentColor="#FFB800"
        />
        <MetricCard
          title="P/C Ratio"
          value={pcRatio}
          accentColor={Number(pcRatio) > 1 ? "#FF3D5A" : "#00E676"}
        />
        <MetricCard
          title="Net GEX"
          value={`${(gex / 1000).toFixed(1)}K`}
          accentColor="#8B5CF6"
        />
        <MetricCard
          title="Max Pain"
          value={`$${maxPainStrike}`}
          accentColor="#00C3FF"
        />
      </div>

      {/* Main body — two columns */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "3fr 2fr",
          gap: 14,
          flex: 1,
          minHeight: 0,
        }}
      >
        {/* Left: chain + IV chart */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            overflow: "auto",
          }}
        >
          <OptionsChain
            underlyingPrice={UNDERLYING_PRICE}
            onStrikeSelect={setStrikeSelected}
          />
          <IVChart underlyingPrice={UNDERLYING_PRICE} />
          {strikeSelected && (
            <div
              style={{
                background: "var(--bg-elevated)",
                border: `1px solid ${ACCENT}30`,
                borderRadius: "var(--radius-md)",
                padding: "8px 12px",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "#8B9AAF",
              }}
            >
              Selected strike:{" "}
              <span style={{ color: "#00C3FF", fontWeight: 700 }}>
                ${strikeSelected}
              </span>
            </div>
          )}
        </div>

        {/* Right: greeks + flow feed */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            overflow: "auto",
          }}
        >
          <GreeksDashboard underlyingPrice={UNDERLYING_PRICE} />
          <UnusualActivityFeed />
        </div>
      </div>
    </motion.div>
  );
}
