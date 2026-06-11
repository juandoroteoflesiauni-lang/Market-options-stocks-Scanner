"use client";
import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { useTradingStore } from "@/store/tradingStore";
import {
  Activity,
  Wifi,
  Server,
  Database,
  ShieldCheck,
  AlertCircle,
  Clock,
} from "lucide-react";
import { formatPct } from "@/utils/format";
import { TickerLogo } from "../panels/TickerLogo";

interface ApiDot {
  label: string;
  connected: boolean;
}

const APIS: ApiDot[] = [
  { label: "BingX", connected: true },
  { label: "Alpaca", connected: true },
  { label: "Binance", connected: true },
  { label: "Options Feed", connected: false },
];

function TickerTape() {
  const universe = useTradingStore((s) => s.universe);
  const tickers = universe.slice(0, 10);

  // Duplicate for seamless loop
  const items = [...tickers, ...tickers];

  return (
    <div className="overflow-hidden flex-1 mx-4" style={{ height: 20 }}>
      <div
        className="flex gap-6 whitespace-nowrap"
        style={{
          animation: "ticker-scroll 40s linear infinite",
          width: "max-content",
        }}
      >
        {items.map((t, i) => (
          <span
            key={i}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#8B9AAF",
            }}
          >
            <TickerLogo symbol={t.symbol} size={12} />
            <span style={{ color: "#00C3FF" }}>{t.symbol}</span>
            <span style={{ color: t.priceChange >= 0 ? "#00E676" : "#FF3D5A" }}>
              {formatPct(t.priceChangePct)}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function StatusBar() {
  const [refresh, setRefresh] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setRefresh(new Date()), 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <footer
      className="fixed bottom-0 left-0 right-0 z-50 flex items-center px-4 gap-3"
      style={{
        height: 28,
        background: "rgba(5,8,16,0.97)",
        borderTop: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      {/* API dots */}
      <div className="flex items-center gap-3 shrink-0">
        {APIS.map((api) => (
          <div key={api.label} className="flex items-center gap-1">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{
                background: api.connected ? "#00E676" : "#FF3D5A",
                animation: api.connected
                  ? "pulse-green 3s ease-in-out infinite"
                  : "strobe-red 1.5s ease-in-out infinite",
              }}
            />
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
              }}
            >
              {api.label}
            </span>
          </div>
        ))}
      </div>

      <span style={{ color: "rgba(255,255,255,0.06)" }}>│</span>

      {/* Last refresh */}
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          flexShrink: 0,
        }}
      >
        REFRESH {refresh.toLocaleTimeString("en-US", { hour12: false })}
      </span>

      {/* Scrolling ticker tape */}
      <TickerTape />

      {/* Version */}
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          flexShrink: 0,
        }}
      >
        FIMA v2.0.0
      </span>
    </footer>
  );
}
