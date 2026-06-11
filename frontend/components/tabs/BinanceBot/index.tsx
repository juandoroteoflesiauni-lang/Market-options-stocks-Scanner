"use client";
import { useState, useMemo, useEffect } from "react";
import { BotStatusStrip } from "../BingXBot/BotStatusStrip";
import { TickerCard } from "../BingXBot/TickerCard";
import { PerformancePanel } from "../BingXBot/PerformancePanel";
import { CandleChart } from "@/components/charts/CandleChart";
import { RiskBar } from "@/components/panels/RiskBar";
import { TickerLogo } from "@/components/panels/TickerLogo";
import {
  getBinanceTickers,
  generatePerfStats,
  generateTrades,
  getCryptoContext,
} from "@/services/mock/bots";
import {
  StaggerContainer,
  StaggerCard,
  staggerContainerProps,
  staggerCardProps,
} from "@/components/layout/TabTransition";

function FearGreedDial({ value, label }: { value: number; label: string }) {
  const size = 90;
  const cx = size / 2;
  const cy = size * 0.65;
  const R = size * 0.38;

  // Half-circle arc — 0=left(fear), 100=right(greed)
  const angle = -180 + (value / 100) * 180;
  const rad = (angle * Math.PI) / 180;
  const nx = cx + R * Math.cos(rad);
  const ny = cy + R * Math.sin(rad);

  const color =
    value < 25
      ? "#FF3D5A"
      : value < 45
        ? "#FF6B35"
        : value < 55
          ? "#FFB800"
          : value < 75
            ? "#00C3FF"
            : "#00E676";

  return (
    <div
      style={{ display: "flex", flexDirection: "column", alignItems: "center" }}
    >
      <svg width={size} height={size * 0.7}>
        {/* Track arc */}
        <path
          d={`M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}`}
          fill="none"
          stroke="rgba(255,255,255,0.08)"
          strokeWidth={8}
          strokeLinecap="round"
        />
        {/* Colored arc */}
        <path
          d={`M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${nx.toFixed(1)} ${ny.toFixed(1)}`}
          fill="none"
          stroke={color}
          strokeWidth={8}
          strokeLinecap="round"
          opacity={0.9}
        />
        {/* Needle */}
        <circle cx={nx} cy={ny} r={4} fill={color} />
        {/* Center value */}
        <text
          x={cx}
          y={cy - 2}
          textAnchor="middle"
          fill="#E8EDF5"
          fontSize={14}
          fontFamily="var(--font-mono)"
          fontWeight="bold"
        >
          {value}
        </text>
      </svg>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color,
          letterSpacing: "0.08em",
          marginTop: 2,
        }}
      >
        {label}
      </span>
    </div>
  );
}

function BtcDominance({ value }: { value: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
          letterSpacing: "0.1em",
        }}
      >
        BTC DOMINANCE
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 22,
          fontWeight: 700,
          color: "#FFB800",
        }}
      >
        {value.toFixed(1)}%
      </span>
      <RiskBar
        value={value / 100}
        warn={0.55}
        danger={0.65}
        showValue={false}
        height={3}
      />
    </div>
  );
}

function FundingStrip({
  tickers,
}: {
  tickers: ReturnType<typeof getBinanceTickers>;
}) {
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {tickers.map((t) => {
        const rate = t.fundingRate ?? 0;
        const color = rate >= 0 ? "#00C3FF" : "#FFB800";
        return (
          <div
            key={t.symbol}
            style={{
              padding: "3px 8px",
              borderRadius: 4,
              background: `${color}10`,
              border: `1px solid ${color}33`,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 1,
            }}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                color: "#4A5568",
              }}
            >
              <TickerLogo symbol={t.symbol} size={12} />
              {t.symbol.replace("-USDT", "")}
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                fontWeight: 600,
                color,
              }}
            >
              {rate >= 0 ? "+" : ""}
              {(rate * 100).toFixed(4)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function BinanceBot() {
  const tickers = useMemo(() => getBinanceTickers(), []);
  const stats = useMemo(() => generatePerfStats(82_000), []);
  const trades = useMemo(
    () =>
      generateTrades(
        tickers.map((t) => t.symbol),
        20,
      ),
    [tickers],
  );
  const ctx = useMemo(() => getCryptoContext(), []);

  const [selectedIdx, setSelectedIdx] = useState(0);
  const selected = tickers[selectedIdx];

  return (
    <StaggerContainer
      {...staggerContainerProps}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        height: "100%",
      }}
    >
      {/* Status strip */}
      <StaggerCard {...staggerCardProps}>
        <BotStatusStrip
          name="BINANCE PERPS BOT v2"
          stats={stats}
          extra={
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 3,
                marginLeft: 12,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  color: "#4A5568",
                  letterSpacing: "0.1em",
                }}
              >
                FUNDING RATES
              </span>
              <FundingStrip tickers={tickers} />
            </div>
          }
        />
      </StaggerCard>

      {/* Ticker cards (8 crypto) */}
      <StaggerCard {...staggerCardProps}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(8, 1fr)",
            gap: 8,
          }}
        >
          {tickers.map((t, i) => (
            <TickerCard
              key={t.symbol}
              ticker={t}
              selected={i === selectedIdx}
              onClick={() => setSelectedIdx(i)}
            />
          ))}
        </div>
      </StaggerCard>

      {/* Bottom row */}
      <StaggerCard {...staggerCardProps} style={{ flex: 1, minHeight: 0 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "55% 1fr 1fr",
            gap: 12,
            height: "100%",
          }}
        >
          {/* Chart */}
          <div
            style={{
              background: "var(--bg-panel)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: "var(--radius-xl)",
              padding: 16,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
                letterSpacing: "0.1em",
                marginBottom: 8,
              }}
            >
              <TickerLogo symbol={selected.symbol} size={14} />
              {selected.symbol} · {selected.leverage ?? "—"}x LEVERAGE
            </div>
            <CandleChart
              ticker={selected.symbol}
              initialPrice={selected.price}
              entryPrice={selected.entryPrice}
              takeProfit={selected.takeProfit}
              stopLoss={selected.stopLoss}
              height={280}
            />

            {/* Liquidation risk */}
            {selected.liquidationDistancePct !== undefined && (
              <div style={{ marginTop: 8 }}>
                <RiskBar
                  value={1 - selected.liquidationDistancePct / 100}
                  warn={0.85}
                  danger={0.95}
                  label={`Liq. Risk — ${selected.liquidationDistancePct.toFixed(1)}% to liq.`}
                />
              </div>
            )}
          </div>

          {/* Crypto context */}
          <div
            style={{
              background: "var(--bg-panel)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: "var(--radius-xl)",
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
                letterSpacing: "0.1em",
              }}
            >
              CRYPTO MARKET CONTEXT
            </div>

            <BtcDominance value={ctx.btcDominance} />

            <div style={{ height: 1, background: "rgba(255,255,255,0.06)" }} />

            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  color: "#4A5568",
                  letterSpacing: "0.1em",
                }}
              >
                FEAR & GREED INDEX
              </span>
              <FearGreedDial
                value={ctx.fearGreedIndex}
                label={ctx.fearGreedLabel}
              />
            </div>

            <div style={{ height: 1, background: "rgba(255,255,255,0.06)" }} />

            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <StatRow label="Total OI" value={`$${ctx.totalOI.toFixed(1)}B`} />
              <StatRow
                label="OI 24h Δ"
                value={`${ctx.oiChange24h >= 0 ? "+" : ""}${ctx.oiChange24h.toFixed(1)}%`}
                color={ctx.oiChange24h >= 0 ? "#00E676" : "#FF3D5A"}
              />
            </div>
          </div>

          {/* Performance */}
          <div
            style={{
              background: "var(--bg-panel)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: "var(--radius-xl)",
              padding: 16,
              overflowY: "auto",
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
                letterSpacing: "0.1em",
                marginBottom: 12,
              }}
            >
              PERFORMANCE
            </div>
            <PerformancePanel stats={stats} trades={trades} />
          </div>
        </div>
      </StaggerCard>
    </StaggerContainer>
  );
}

function StatRow({
  label,
  value,
  color = "#E8EDF5",
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between" }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 600,
          color,
        }}
      >
        {value}
      </span>
    </div>
  );
}
