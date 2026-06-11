"use client";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  IndicatorPanel,
  DEFAULT_STATE,
  type IndicatorState,
} from "./IndicatorPanel";
import { MainChart } from "./MainChart";
import { SignalMatrix } from "./SignalMatrix";
import { KeyLevels } from "./KeyLevels";
import { generateGBM } from "@/services/mock/gbm";
import { generateOptionsChain } from "@/services/mock/optionsChain";
import { TickerLogo } from "@/components/panels/TickerLogo";

const ACCENT = "#FFB800";

const TICKERS = [
  { symbol: "AAPL", price: 194.82, iv: 0.28 },
  { symbol: "MSFT", price: 418.31, iv: 0.24 },
  { symbol: "TSLA", price: 248.7, iv: 0.62 },
  { symbol: "NVDA", price: 878.45, iv: 0.48 },
  { symbol: "META", price: 492.17, iv: 0.31 },
  { symbol: "GOOGL", price: 177.4, iv: 0.26 },
  { symbol: "SPY", price: 531.2, iv: 0.15 },
  { symbol: "QQQ", price: 445.9, iv: 0.18 },
];

export function Technical() {
  const [selectedSymbol, setSelectedSymbol] = useState("AAPL");
  const [indicators, setIndicators] = useState<IndicatorState>(DEFAULT_STATE);

  const ticker = TICKERS.find((t) => t.symbol === selectedSymbol) ?? TICKERS[0];

  const candles = useMemo(
    () => generateGBM(ticker.price, 120, 0.0001, ticker.iv * 0.05),
    [ticker.price, ticker.iv],
  );
  const chain = useMemo(
    () => generateOptionsChain(ticker.price, ticker.iv),
    [ticker.price, ticker.iv],
  );

  const spot = candles[candles.length - 1]?.close ?? ticker.price;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        height: "100%",
        padding: 0,
      }}
    >
      {/* ── Header ─────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: ACCENT,
              padding: "1px 6px",
              border: `1px solid ${ACCENT}40`,
              borderRadius: "var(--radius-sm)",
              background: `${ACCENT}10`,
              letterSpacing: "0.1em",
            }}
          >
            07
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              color: "#E8EDF5",
            }}
          >
            Technical Analysis Terminal
          </span>
        </div>

        {/* Ticker selector */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TickerLogo symbol={selectedSymbol} size={20} />
          <select
            value={selectedSymbol}
            onChange={(e) => setSelectedSymbol(e.target.value)}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "#E8EDF5",
              background: "var(--bg-elevated)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: "var(--radius-sm)",
              padding: "4px 8px",
              cursor: "pointer",
              outline: "none",
            }}
          >
            {TICKERS.map((t) => (
              <option key={t.symbol} value={t.symbol}>
                {t.symbol}
              </option>
            ))}
          </select>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              fontWeight: 700,
              color: "#00C3FF",
            }}
          >
            ${spot.toFixed(2)}
          </span>
        </div>
      </div>

      {/* ── 3-column layout ────────────────────────────────────── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "200px 1fr 220px",
          gap: 8,
          flex: 1,
          minHeight: 0,
        }}
      >
        {/* Left: indicator toggles */}
        <div style={{ overflow: "auto" }}>
          <IndicatorPanel state={indicators} onChange={setIndicators} />
        </div>

        {/* Center: main chart */}
        <div style={{ minHeight: 0 }}>
          <MainChart candles={candles} indicators={indicators} spot={spot} />
        </div>

        {/* Right: signal matrix + key levels */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            overflow: "auto",
          }}
        >
          <SignalMatrix indicators={indicators} />
          <KeyLevels candles={candles} chain={chain} spot={spot} />
        </div>
      </div>
    </motion.div>
  );
}
