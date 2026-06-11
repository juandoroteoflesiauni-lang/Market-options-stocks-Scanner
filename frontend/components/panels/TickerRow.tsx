"use client";
import { memo } from "react";
import { TrendingUp, TrendingDown } from "lucide-react";
import type { Ticker } from "@/types";
import { PhaseTag } from "./PhaseTag";
import { LightweightChart } from "./LightweightChart";
import { MetricCard } from "./MetricCard";
import { TickerLogo } from "./TickerLogo";
import { signalColor } from "@/utils/colors";
import { formatPrice, formatVolume, formatPct } from "@/utils/format";

interface Props {
  ticker: Ticker;
  onSelect: (symbol: string) => void;
}

// ... SignalDots, AccordionDetail functions remain unchanged but skipped for brevity here

function SignalDots({ ticker }: { ticker: Ticker }) {
  const bullCount = ticker.signals.filter((s) => s.direction === "BULL").length;
  const bearCount = ticker.signals.filter((s) => s.direction === "BEAR").length;
  return (
    <div style={{ display: "flex", gap: 2, alignItems: "center" }}>
      {ticker.signals.slice(0, 6).map((s, i) => (
        <span
          key={i}
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: signalColor(s.direction),
            display: "inline-block",
            opacity: 0.8,
          }}
          title={`${s.name}: ${s.direction}`}
        />
      ))}
    </div>
  );
}

export const TickerRow = memo(function TickerRow({ ticker, onSelect }: Props) {
  const up = ticker.priceChange >= 0;
  const deltaColor = up ? "#00E676" : "#FF3D5A";

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        transition: "border-color 0.15s ease",
      }}
    >
      {/* Main row */}
      <div
        onClick={() => onSelect(ticker.symbol)}
        style={{
          display: "grid",
          gridTemplateColumns:
            "110px 1fr 1fr 70px 70px 60px 90px 100px 70px 28px",
          alignItems: "center",
          padding: "10px 12px",
          cursor: "pointer",
          gap: 8,
          transition: "background 0.08s ease",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.background = "transparent";
        }}
      >
        {/* Symbol */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TickerLogo symbol={ticker.symbol} size={20} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              fontWeight: 700,
              color: "#00C3FF",
              letterSpacing: "0.04em",
            }}
          >
            {ticker.symbol}
          </span>
        </div>

        {/* Price + delta */}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              fontWeight: 600,
              color: "#E8EDF5",
            }}
          >
            ${formatPrice(ticker.price)}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
            {up ? (
              <TrendingUp size={10} color={deltaColor} />
            ) : (
              <TrendingDown size={10} color={deltaColor} />
            )}
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: deltaColor,
              }}
            >
              {formatPct(ticker.priceChangePct)}
            </span>
          </div>
        </div>

        {/* After Market */}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.08em",
            }}
          >
            EXT
          </span>
          {ticker.afterMarketPrice !== undefined ? (
            <>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "#E8EDF5",
                }}
              >
                ${formatPrice(ticker.afterMarketPrice)}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color:
                    ticker.afterMarketChangePct! >= 0 ? "#00E676" : "#FF3D5A",
                }}
              >
                {ticker.afterMarketChangePct! >= 0 ? "+" : ""}
                {formatPct(ticker.afterMarketChangePct!)}
              </span>
            </>
          ) : (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "#8B9AAF",
              }}
            >
              -
            </span>
          )}
        </div>

        {/* Volume */}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.08em",
            }}
          >
            VOL
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#8B9AAF",
            }}
          >
            {formatVolume(ticker.volume)}
          </span>
        </div>

        {/* IV */}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.08em",
            }}
          >
            IV
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#FFB800",
            }}
          >
            {(ticker.iv * 100).toFixed(1)}%
          </span>
        </div>

        {/* IV Rank */}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.08em",
            }}
          >
            IVR
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#8B9AAF",
            }}
          >
            {ticker.ivRank}
          </span>
        </div>

        {/* Phase tag */}
        <PhaseTag phase={ticker.phase} compact />

        {/* Candlesticks */}
        <LightweightChart data={ticker.candles} width={100} height={32} />

        {/* Signal dots */}
        <SignalDots ticker={ticker} />

        {/* Chevron -> Replaced with an expand/maximize icon */}
        <span style={{ display: "flex", color: "#4A5568" }}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="15 3 21 3 21 9"></polyline>
            <polyline points="9 21 3 21 3 15"></polyline>
            <line x1="21" y1="3" x2="14" y2="10"></line>
            <line x1="3" y1="21" x2="10" y2="14"></line>
          </svg>
        </span>
      </div>
    </div>
  );
});
