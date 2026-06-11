"use client";

import { memo, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { AdvancedRealTimeChart } from "react-ts-tradingview-widgets";
import { MetricCard } from "./MetricCard";
import { signalColor } from "@/utils/colors";
import { formatPrice, formatVolume } from "@/utils/format";
import type { Ticker } from "@/types";

interface Props {
  ticker: Ticker;
  isVisible: boolean;
  onClose: () => void;
}

export const TickerModal = memo(function TickerModal({
  ticker,
  isVisible,
  onClose,
}: Props) {
  // Lock body scroll when open
  useEffect(() => {
    if (isVisible) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "unset";
    }
    return () => {
      document.body.style.overflow = "unset";
    };
  }, [isVisible]);

  return (
    <motion.div
      initial={false}
      animate={{
        opacity: isVisible ? 1 : 0,
        pointerEvents: isVisible ? "auto" : "none",
      }}
      transition={{ duration: 0.2 }}
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0,0,0,0.7)",
        backdropFilter: "blur(4px)",
        zIndex: isVisible ? 9999 : -1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
      }}
      onClick={onClose}
    >
      <motion.div
        initial={false}
        animate={{
          y: isVisible ? 0 : 50,
          scale: isVisible ? 1 : 0.95,
        }}
        transition={{ type: "spring", damping: 25, stiffness: 300 }}
        style={{
          background: "var(--bg-panel)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: "var(--radius-lg)",
          width: "100%",
          maxWidth: "1400px",
          height: "90vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "0 25px 50px -12px rgba(0,0,0,0.5)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 24px",
            borderBottom: "1px solid rgba(255,255,255,0.05)",
            background: "rgba(255,255,255,0.02)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <h2
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 20,
                fontWeight: 700,
                color: "#00C3FF",
                margin: 0,
              }}
            >
              {ticker.symbol}
            </h2>
            <span style={{ fontSize: 14, color: "#8B9AAF" }}>
              Technical Analysis & Signals
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "#8B9AAF",
              cursor: "pointer",
              padding: 4,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "4px",
              transition: "all 0.2s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "#FFF";
              e.currentTarget.style.background = "rgba(255,255,255,0.1)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "#8B9AAF";
              e.currentTarget.style.background = "transparent";
            }}
          >
            <X size={24} />
          </button>
        </div>

        {/* Content Body: 3/4 Chart, 1/4 Data */}
        <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
          {/* Left Column: Advanced Chart */}
          <div
            style={{
              flex: "0 0 75%",
              borderRight: "1px solid rgba(255,255,255,0.05)",
              background: "#131722",
            }}
          >
            <AdvancedRealTimeChart
              symbol={ticker.symbol}
              theme="dark"
              autosize
              allow_symbol_change={false}
              hide_top_toolbar={false}
              hide_side_toolbar={false}
              enable_publishing={false}
              details={false}
              hotlist={false}
              calendar={false}
              interval="1"
              timezone="Etc/UTC"
              style="1" // 1 = Candles
              toolbar_bg="#131722"
            />
          </div>

          {/* Right Column: Signals & Metrics */}
          <div
            style={{
              flex: "0 0 25%",
              padding: "20px",
              overflowY: "auto",
              background: "rgba(0,0,0,0.2)",
            }}
          >
            {/* Metrics Grid */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 12,
                marginBottom: 24,
              }}
            >
              <MetricCard
                title="Last Price"
                value={`$${formatPrice(ticker.price)}`}
                delta={ticker.priceChangePct}
                deltaLabel="today"
                sparkline={ticker.candles.map((c) => c.close)}
              />
              <MetricCard
                title="IV Rank"
                value={ticker.ivRank}
                unit="%"
                accentColor="#FFB800"
              />
              <MetricCard
                title="Volume"
                value={formatVolume(ticker.volume)}
                deltaLabel={`avg ${formatVolume(ticker.avgVolume)}`}
                accentColor="#7C3AED"
              />
              <MetricCard
                title="Momentum"
                value={
                  ticker.momentum > 0 ? `+${ticker.momentum}` : ticker.momentum
                }
                delta={ticker.momentum}
                accentColor={ticker.momentum >= 0 ? "#00E676" : "#FF3D5A"}
              />
            </div>

            {/* Signals Table */}
            <div
              style={{
                background: "rgba(255,255,255,0.02)",
                borderRadius: "var(--radius-md)",
                border: "1px solid rgba(255,255,255,0.05)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 60px 70px 50px",
                  padding: "8px 12px",
                  borderBottom: "1px solid rgba(255,255,255,0.05)",
                  background: "rgba(255,255,255,0.03)",
                }}
              >
                {["INDICATOR", "VAL", "SIGNAL", "WT"].map((h) => (
                  <span
                    key={h}
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      color: "#4A5568",
                      letterSpacing: "0.1em",
                    }}
                  >
                    {h}
                  </span>
                ))}
              </div>
              {ticker.signals.map((sig, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 60px 70px 50px",
                    padding: "6px 12px",
                    borderBottom:
                      i < ticker.signals.length - 1
                        ? "1px solid rgba(255,255,255,0.03)"
                        : "none",
                    background:
                      i % 2 === 1 ? "rgba(255,255,255,0.01)" : "transparent",
                    alignItems: "center",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "#8B9AAF",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {sig.name}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "#E8EDF5",
                    }}
                  >
                    {sig.value}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: signalColor(sig.direction),
                    }}
                  >
                    {sig.direction === "BULL"
                      ? "▲"
                      : sig.direction === "BEAR"
                        ? "▼"
                        : "→"}{" "}
                    {sig.direction}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "#4A5568",
                    }}
                  >
                    {sig.weight}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
});
