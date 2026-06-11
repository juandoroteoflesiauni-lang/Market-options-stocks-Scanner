"use client";
import { memo, useEffect, useState } from "react";
import { TrendingUp, TrendingDown } from "lucide-react";
import type { BotTicker } from "@/services/mock/bots";
import { TickerLogo } from "@/components/panels/TickerLogo";
import { formatPrice, formatPct } from "@/utils/format";

interface Props {
  ticker: BotTicker;
  selected?: boolean;
  onClick?: () => void;
}

function borderColor(t: BotTicker): string {
  if (t.direction === "LONG" && t.unrealizedPnL >= 0)
    return "rgba(0,230,118,0.4)";
  if (t.direction === "SHORT" && t.unrealizedPnL >= 0)
    return "rgba(0,230,118,0.4)";
  if (t.unrealizedPnL < 0) return "rgba(255,61,90,0.4)";
  return "rgba(255,184,0,0.3)";
}

function glowColor(t: BotTicker): string {
  if (t.unrealizedPnL >= 0) return "rgba(0,230,118,0.08)";
  return "rgba(255,61,90,0.06)";
}

function Row({
  label,
  value,
  color = "#8B9AAF",
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
        }}
      >
        {label}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color }}>
        {value}
      </span>
    </div>
  );
}

export const TickerCard = memo(function TickerCard({
  ticker: t,
  selected,
  onClick,
}: Props) {
  const [price, setPrice] = useState(t.price);
  const [flash, setFlash] = useState("");

  // Simulate live price ticks
  useEffect(() => {
    const id = setInterval(
      () => {
        setPrice((prev) => {
          const next = +(prev * (1 + (Math.random() - 0.49) * 0.002)).toFixed(
            2,
          );
          setFlash(next > prev ? "price-flash-up" : "price-flash-down");
          setTimeout(() => setFlash(""), 400);
          return next;
        });
      },
      100 + Math.random() * 250,
    );
    return () => clearInterval(id);
  }, []);

  const pnlColor = t.unrealizedPnL >= 0 ? "#00E676" : "#FF3D5A";
  const dirColor = t.direction === "LONG" ? "#00E676" : "#FF3D5A";

  return (
    <div
      onClick={onClick}
      style={{
        padding: "12px",
        background: selected ? `${glowColor(t)}` : "var(--bg-panel)",
        border: `1px solid ${selected ? borderColor(t) : "rgba(255,255,255,0.08)"}`,
        borderRadius: "var(--radius-lg)",
        cursor: "pointer",
        transition: "all 0.15s ease",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        boxShadow: selected ? `0 0 12px ${glowColor(t)}` : "none",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <TickerLogo symbol={t.symbol} size={20} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 700,
              color: "#00C3FF",
            }}
          >
            {t.symbol}
          </span>
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            fontWeight: 600,
            color: dirColor,
            background: `${dirColor}18`,
            border: `1px solid ${dirColor}44`,
            borderRadius: "var(--radius-pill)",
            padding: "1px 8px",
            letterSpacing: "0.06em",
          }}
        >
          {t.direction}
        </span>
      </div>

      {/* Price */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span
          className={flash}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 18,
            fontWeight: 700,
            color: "#E8EDF5",
          }}
        >
          ${formatPrice(price)}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
          {t.unrealizedPnLPct >= 0 ? (
            <TrendingUp size={11} color="#00E676" />
          ) : (
            <TrendingDown size={11} color="#FF3D5A" />
          )}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: pnlColor,
            }}
          >
            {formatPct(t.unrealizedPnLPct)}
          </span>
        </div>
      </div>

      <div style={{ height: 1, background: "rgba(255,255,255,0.06)" }} />

      {/* Position details */}
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        <Row label="Entry" value={`$${formatPrice(t.entryPrice)}`} />
        <Row
          label="TP"
          value={`$${formatPrice(t.takeProfit)}`}
          color="#00E676"
        />
        <Row label="SL" value={`$${formatPrice(t.stopLoss)}`} color="#FF3D5A" />
        <Row label="Size" value={`${t.size} ${t.sizeUnit}`} />
        <Row
          label="UnrPnL"
          value={`${t.unrealizedPnL >= 0 ? "+" : ""}$${Math.abs(t.unrealizedPnL).toFixed(2)}`}
          color={pnlColor}
        />
      </div>

      <div style={{ height: 1, background: "rgba(255,255,255,0.06)" }} />

      {/* Greeks */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 3 }}>
        <GreekCell symbol="Δ" value={t.delta.toFixed(3)} />
        <GreekCell symbol="Γ" value={t.gamma.toFixed(4)} />
        <GreekCell symbol="Θ" value={t.theta.toFixed(3)} />
        <GreekCell symbol="IV" value={`${t.iv.toFixed(1)}%`} color="#FFB800" />
      </div>

      <div style={{ height: 1, background: "rgba(255,255,255,0.06)" }} />

      {/* Technicals */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 3 }}>
        <MiniStat
          label="RSI"
          value={t.rsi.toString()}
          color={t.rsi > 70 ? "#FF3D5A" : t.rsi < 30 ? "#00E676" : "#8B9AAF"}
        />
        <MiniStat
          label="MACD"
          value={t.macdBull ? "▲ bull" : "▼ bear"}
          color={t.macdBull ? "#00E676" : "#FF3D5A"}
        />
        <MiniStat
          label="Vol"
          value={`${t.volRatio}x avg`}
          color={t.volRatio > 1.5 ? "#FFB800" : "#8B9AAF"}
        />
        <MiniStat
          label="ATR"
          value={t.atrOk ? "ok" : "high"}
          color={t.atrOk ? "#8B9AAF" : "#FFB800"}
        />
      </div>

      {/* Extra fields (leverage, funding, etc.) */}
      {t.leverage && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "4px 6px",
            background: "rgba(255,184,0,0.08)",
            borderRadius: 4,
            border: "1px solid rgba(255,184,0,0.2)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#FFB800",
            }}
          >
            LEVERAGE
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 600,
              color: "#FFB800",
            }}
          >
            {t.leverage}x
          </span>
        </div>
      )}
      {t.liquidationDistancePct !== undefined && (
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
            }}
          >
            Liq. Distance
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color:
                t.liquidationDistancePct < 5
                  ? "#FF3D5A"
                  : t.liquidationDistancePct < 10
                    ? "#FFB800"
                    : "#8B9AAF",
            }}
          >
            {t.liquidationDistancePct.toFixed(1)}%
          </span>
        </div>
      )}
      {t.fundingCountdown !== undefined && (
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
            }}
          >
            Funding in
          </span>
          <FundingCountdown
            seconds={t.fundingCountdown}
            rate={t.fundingRate ?? 0}
          />
        </div>
      )}
    </div>
  );
});

function GreekCell({
  symbol,
  value,
  color = "#00C3FF",
}: {
  symbol: string;
  value: string;
  color?: string;
}) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
        }}
      >
        {symbol}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color }}>
        {value}
      </span>
    </div>
  );
}

function MiniStat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: "#4A5568",
          minWidth: 24,
        }}
      >
        {label}:
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color }}>
        {value}
      </span>
    </div>
  );
}

function FundingCountdown({
  seconds,
  rate,
}: {
  seconds: number;
  rate: number;
}) {
  const [s, setS] = useState(seconds);
  useEffect(() => {
    const id = setInterval(
      () => setS((prev) => (prev <= 0 ? 28800 : prev - 1)),
      1000,
    );
    return () => clearInterval(id);
  }, []);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const color = rate >= 0 ? "#00C3FF" : "#FFB800";
  return (
    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color }}>
      {String(h).padStart(2, "0")}:{String(m).padStart(2, "0")}:
      {String(sec).padStart(2, "0")}
    </span>
  );
}
