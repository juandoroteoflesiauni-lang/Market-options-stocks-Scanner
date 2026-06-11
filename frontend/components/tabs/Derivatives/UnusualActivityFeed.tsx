"use client";
import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  INITIAL_FLOW,
  addNewFlowRow,
  type UnusualFlowRow,
  type PremiumTier,
} from "@/data/unusualActivity";
import { formatCurrency } from "@/utils/format";
import { TickerLogo } from "@/components/panels/TickerLogo";

const TYPE_COLORS: Record<string, string> = {
  SWEEP: "#00C3FF",
  BLOCK: "#FFB800",
  SPLIT: "#8B5CF6",
};

const FILTERS: { label: string; value: PremiumTier }[] = [
  { label: "$10K+", value: 10_000 },
  { label: "$50K+", value: 50_000 },
  { label: "$100K+", value: 100_000 },
];

function FlowRow({ row }: { row: UnusualFlowRow }) {
  const isCall = row.side === "CALL";
  const sideColor = isCall ? "#00C3FF" : "#FF3D5A";
  const typeColor = TYPE_COLORS[row.type] ?? "#8B9AAF";
  const sentColor =
    row.sentiment === "BULLISH"
      ? "#00E676"
      : row.sentiment === "BEARISH"
        ? "#FF3D5A"
        : "#8B9AAF";
  const timeStr = row.timestamp.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 12 }}
      transition={{ duration: 0.25 }}
      style={{
        display: "grid",
        gridTemplateColumns: "44px 60px 42px 48px 52px 70px 50px 44px",
        gap: 0,
        padding: "4px 8px",
        borderBottom: "1px solid rgba(255,255,255,0.03)",
        alignItems: "center",
        background:
          row.premium >= 100_000
            ? "rgba(255,184,0,0.04)"
            : row.premium >= 50_000
              ? "rgba(0,195,255,0.02)"
              : "transparent",
        transition: "background 0.2s ease",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
        }}
      >
        {timeStr}
      </span>
      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <TickerLogo symbol={row.symbol} size={14} />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 700,
            color: "#E8EDF5",
          }}
        >
          {row.symbol}
        </span>
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          fontWeight: 600,
          color: sideColor,
          padding: "1px 4px",
          background: `${sideColor}15`,
          borderRadius: 2,
          textAlign: "center",
        }}
      >
        {row.side}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#E8EDF5",
        }}
      >
        ${row.strike}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#8B9AAF",
        }}
      >
        {row.expiry}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 700,
          color:
            row.premium >= 100_000
              ? "#FFB800"
              : row.premium >= 50_000
                ? "#00C3FF"
                : "#E8EDF5",
          textAlign: "right",
        }}
      >
        {formatCurrency(row.premium, true)}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: typeColor,
          padding: "1px 4px",
          background: `${typeColor}15`,
          borderRadius: 2,
          textAlign: "center",
        }}
      >
        {row.type}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: sentColor,
          textAlign: "center",
        }}
      >
        {row.sentiment === "BULLISH"
          ? "▲"
          : row.sentiment === "BEARISH"
            ? "▼"
            : "–"}
      </span>
    </motion.div>
  );
}

export function UnusualActivityFeed() {
  const [filter, setFilter] = useState<PremiumTier>(10_000);
  const [rows, setRows] = useState<UnusualFlowRow[]>(INITIAL_FLOW);
  const [paused, setPaused] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => {
      setRows((prev) => addNewFlowRow(prev));
    }, 3500);
    return () => clearInterval(id);
  }, [paused]);

  const visible = rows.filter((r) => r.premium >= filter).slice(0, 30);

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        flex: 1,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          Unusual Flow Feed
        </span>
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                padding: "2px 7px",
                border: "1px solid",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                transition: "all 0.15s ease",
                borderColor:
                  filter === f.value ? "#FF3D5A" : "rgba(255,255,255,0.08)",
                background: filter === f.value ? "#FF3D5A15" : "transparent",
                color: filter === f.value ? "#FF3D5A" : "#4A5568",
              }}
            >
              {f.label}
            </button>
          ))}
          <button
            onClick={() => setPaused((p) => !p)}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              padding: "2px 8px",
              border: "1px solid",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              marginLeft: 4,
              borderColor: paused ? "#FFB800" : "rgba(255,255,255,0.08)",
              background: paused ? "#FFB80015" : "transparent",
              color: paused ? "#FFB800" : "#4A5568",
            }}
          >
            {paused ? "▶ RESUME" : "⏸ PAUSE"}
          </button>
        </div>
      </div>

      {/* Column headers */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "44px 60px 42px 48px 52px 70px 50px 44px",
          gap: 0,
          padding: "5px 8px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: "var(--bg-elevated)",
        }}
      >
        {[
          "Time",
          "Symbol",
          "Side",
          "Strike",
          "Expiry",
          "Premium",
          "Type",
          "Sent",
        ].map((h) => (
          <span
            key={h}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 8,
              color: "#4A5568",
              letterSpacing: "0.1em",
              textTransform: "uppercase",
            }}
          >
            {h}
          </span>
        ))}
      </div>

      {/* Feed */}
      <div ref={scrollRef} style={{ overflowY: "auto", flex: 1, minHeight: 0 }}>
        <AnimatePresence initial={false}>
          {visible.map((row) => (
            <FlowRow key={row.id} row={row} />
          ))}
        </AnimatePresence>
        {visible.length === 0 && (
          <div
            style={{
              padding: 32,
              textAlign: "center",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#4A5568",
            }}
          >
            No flows above {formatCurrency(filter, true)} threshold
          </div>
        )}
      </div>
    </div>
  );
}
