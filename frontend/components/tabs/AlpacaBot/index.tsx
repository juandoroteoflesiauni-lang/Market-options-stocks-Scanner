"use client";
import { useState, useMemo } from "react";
import { AlertTriangle, Clock } from "lucide-react";
import { BotStatusStrip } from "../BingXBot/BotStatusStrip";
import { TickerCard } from "../BingXBot/TickerCard";
import { PerformancePanel } from "../BingXBot/PerformancePanel";
import { CandleChart } from "@/components/charts/CandleChart";
import { DataTable, type Column } from "@/components/panels/DataTable";
import { TickerLogo } from "@/components/panels/TickerLogo";
import {
  getAlpacaTickers,
  generatePerfStats,
  generateTrades,
  getPDTStatus,
} from "@/services/mock/bots";
import {
  StaggerContainer,
  StaggerCard,
  staggerContainerProps,
  staggerCardProps,
} from "@/components/layout/TabTransition";

interface OptionsRow {
  strike: number;
  expiry: string;
  type: "CALL" | "PUT";
  volume: number;
  oi: number;
  iv: string;
  delta: string;
  note: string;
}

const MOCK_OPTIONS: OptionsRow[] = [
  {
    strike: 195,
    expiry: "06/21",
    type: "CALL",
    volume: 4200,
    oi: 1200,
    iv: "28.4%",
    delta: "+0.54",
    note: "UNUSUAL ▲",
  },
  {
    strike: 190,
    expiry: "06/21",
    type: "PUT",
    volume: 890,
    oi: 3100,
    iv: "31.2%",
    delta: "-0.38",
    note: "",
  },
  {
    strike: 420,
    expiry: "07/19",
    type: "CALL",
    volume: 6800,
    oi: 2400,
    iv: "24.1%",
    delta: "+0.61",
    note: "UNUSUAL ▲",
  },
  {
    strike: 415,
    expiry: "07/19",
    type: "PUT",
    volume: 440,
    oi: 980,
    iv: "26.5%",
    delta: "-0.42",
    note: "",
  },
  {
    strike: 250,
    expiry: "06/21",
    type: "CALL",
    volume: 12000,
    oi: 4200,
    iv: "58.3%",
    delta: "+0.48",
    note: "UNUSUAL ▲",
  },
  {
    strike: 240,
    expiry: "06/21",
    type: "PUT",
    volume: 2200,
    oi: 1800,
    iv: "62.1%",
    delta: "-0.55",
    note: "",
  },
];

const OPT_COLS: Column<OptionsRow>[] = [
  { key: "strike", header: "STRIKE", align: "right", width: 55 },
  { key: "expiry", header: "EXPIRY", width: 55 },
  {
    key: "type",
    header: "TYPE",
    width: 50,
    render: (r) => (
      <span
        style={{
          color: r.type === "CALL" ? "#00E676" : "#FF3D5A",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
        }}
      >
        {r.type}
      </span>
    ),
  },
  { key: "volume", header: "VOL", align: "right", width: 55 },
  { key: "oi", header: "OI", align: "right", width: 50 },
  {
    key: "iv",
    header: "IV",
    align: "right",
    width: 55,
    render: (r) => (
      <span
        style={{
          color: "#FFB800",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
        }}
      >
        {r.iv}
      </span>
    ),
  },
  {
    key: "delta",
    header: "DELTA",
    align: "right",
    width: 55,
    render: (r) => (
      <span
        style={{
          color: "#00C3FF",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
        }}
      >
        {r.delta}
      </span>
    ),
  },
  {
    key: "note",
    header: "NOTE",
    render: (r) => (
      <span
        style={{
          color: "#FFB800",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
        }}
      >
        {r.note}
      </span>
    ),
  },
];

function formatCountdown(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function getSession(): { label: string; color: string } {
  const h = new Date().getUTCHours();
  if (h >= 9 && h < 13) return { label: "PRE", color: "#FFB800" };
  if (h >= 13 && h < 20) return { label: "OPEN", color: "#00E676" };
  return { label: "AFTER", color: "#FFB800" };
}

export function AlpacaBot() {
  const tickers = useMemo(() => getAlpacaTickers(), []);
  const stats = useMemo(() => generatePerfStats(52_000), []);
  const trades = useMemo(
    () =>
      generateTrades(
        tickers.map((t) => t.symbol),
        20,
      ),
    [tickers],
  );
  const pdt = useMemo(() => getPDTStatus(), []);
  const session = getSession();

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
          name="ALPACA EQUITY BOT v2"
          stats={stats}
          extra={
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                marginLeft: 8,
              }}
            >
              {/* PDT counter */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  background:
                    pdt.dayTradesRemaining <= 1
                      ? "rgba(255,184,0,0.12)"
                      : "rgba(255,255,255,0.04)",
                  border: `1px solid ${pdt.dayTradesRemaining <= 1 ? "rgba(255,184,0,0.4)" : "rgba(255,255,255,0.08)"}`,
                  borderRadius: 6,
                }}
              >
                {pdt.dayTradesRemaining <= 1 && (
                  <AlertTriangle size={11} color="#FFB800" />
                )}
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    color: "#4A5568",
                  }}
                >
                  DAY TRADES
                </span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    fontWeight: 600,
                    color: pdt.dayTradesRemaining <= 1 ? "#FFB800" : "#E8EDF5",
                  }}
                >
                  {pdt.dayTradesUsed}/3
                </span>
              </div>

              {/* Session */}
              <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    color: "#4A5568",
                  }}
                >
                  SESSION
                </span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    fontWeight: 600,
                    color: session.color,
                  }}
                >
                  {session.label}
                </span>
              </div>

              {/* Buying power */}
              <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    color: "#4A5568",
                  }}
                >
                  BUYING PWR
                </span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    fontWeight: 600,
                    color: "#E8EDF5",
                  }}
                >
                  $
                  {pdt.buyingPower.toLocaleString("en-US", {
                    maximumFractionDigits: 0,
                  })}
                </span>
              </div>

              {/* PDT badge */}
              {pdt.isPatternDayTrader && (
                <div
                  style={{
                    padding: "2px 8px",
                    background: "rgba(255,61,90,0.15)",
                    border: "1px solid rgba(255,61,90,0.4)",
                    borderRadius: "var(--radius-pill)",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      color: "#FF3D5A",
                      letterSpacing: "0.1em",
                    }}
                  >
                    PDT
                  </span>
                </div>
              )}
            </div>
          }
        />
      </StaggerCard>

      {/* Ticker cards (8) */}
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

      {/* Options Activity Monitor */}
      <StaggerCard {...staggerCardProps}>
        <div
          style={{
            background: "var(--bg-panel)",
            border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: "var(--radius-lg)",
            padding: "12px 16px",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 8,
            }}
          >
            OPTIONS ACTIVITY MONITOR — unusual flow highlighted
          </div>
          <DataTable<OptionsRow>
            columns={OPT_COLS}
            data={MOCK_OPTIONS}
            rowKey={(_, i) => i}
            maxHeight={180}
          />
        </div>
      </StaggerCard>

      {/* Bottom: Chart + Performance */}
      <StaggerCard {...staggerCardProps} style={{ flex: 1, minHeight: 0 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "60% 40%",
            gap: 12,
            height: "100%",
          }}
        >
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
              <span>
                LIVE CHART — {selected.symbol} · {selected.orderType}
              </span>
              {selected.fractionalSize && (
                <span style={{ color: "#8B9AAF" }}>
                  ({selected.fractionalSize})
                </span>
              )}
            </div>
            <CandleChart
              ticker={selected.symbol}
              initialPrice={selected.price}
              entryPrice={selected.entryPrice}
              takeProfit={selected.takeProfit}
              stopLoss={selected.stopLoss}
              height={260}
            />
          </div>

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
