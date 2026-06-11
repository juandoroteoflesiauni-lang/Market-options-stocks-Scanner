"use client";
import React, { useState, useMemo } from "react";
import { TickerRow } from "@/components/panels/TickerRow";
import { TickerModal } from "@/components/panels/TickerModal";
import { UniverseManager } from "./UniverseManager";
import { StrategyWeights } from "./StrategyWeights";
import { PhaseAnalytics } from "./PhaseAnalytics";
import { useScanner } from "@/hooks/useScanner";
import { useScannerWebSocket } from "@/hooks/useScannerWebSocket";
import {
  displayListToTickers,
  displayToTicker,
} from "@/services/scannerService";
import { DIRECTION_COLORS } from "@/lib/constants";
import {
  StaggerContainer,
  StaggerCard,
  staggerContainerProps,
  staggerCardProps,
} from "@/components/layout/TabTransition";
import type { ScannerTickerDisplay } from "@/types/marketScanner";
import type { Ticker } from "@/types";

const PHASES = ["A", "B", "C", "D"] as const;
type SortKey =
  | "phase"
  | "scanner_score"
  | "intraday_score"
  | "swing_score"
  | "change_pct";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "phase", label: "Phase" },
  { key: "scanner_score", label: "Score" },
  { key: "intraday_score", label: "Intraday" },
  { key: "swing_score", label: "Swing" },
  { key: "change_pct", label: "Change %" },
];

const PANEL: React.CSSProperties = {
  background: "var(--bg-panel)",
  border: "1px solid rgba(255,255,255,0.06)",
  borderRadius: "var(--radius-xl)",
  padding: 16,
  height: "100%",
  overflowY: "auto",
};

export function MarketScanner() {
  const {
    tickers,
    universes,
    livePrices,
    isScanning,
    isLoading,
    error,
    isConnected,
    selectedUniverse,
    scan,
    setUniverse,
    updateParams,
    clearError,
    retry,
  } = useScanner();

  // Real-time price updates via WebSocket (replaces 3s HTTP polling)
  useScannerWebSocket();

  const [phaseFilter, setPhase] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("scanner_score");
  const [sortAsc, setSortAsc] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);

  // Convert backend tickers to legacy Ticker[] for TickerRow/TickerModal
  const allTickers: Ticker[] = useMemo(
    () => displayListToTickers(tickers),
    [tickers],
  );

  const filtered: ScannerTickerDisplay[] = useMemo(() => {
    let list =
      phaseFilter.size > 0
        ? tickers.filter((t) => phaseFilter.has(t.phase))
        : tickers;

    list = [...list].sort((a, b) => {
      const sortMap: Record<SortKey, (t: ScannerTickerDisplay) => number> = {
        phase: (t) => t.phase.charCodeAt(0),
        scanner_score: (t) => parseFloat(t.scanner_score) || 0,
        intraday_score: (t) => parseFloat(t.intraday_score) || 0,
        swing_score: (t) => parseFloat(t.swing_score) || 0,
        change_pct: (t) => parseFloat(t.change_pct) || 0,
      };
      const getter = sortMap[sortKey];
      const av = getter(a);
      const bv = getter(b);
      return sortAsc ? av - bv : bv - av;
    });

    return list;
  }, [tickers, phaseFilter, sortKey, sortAsc]);

  // Map filtered ScannerTickerDisplay to Ticker for TickerRow, with live price overrides
  const filteredTickers: Ticker[] = useMemo(
    () =>
      filtered.map((d) => {
        const ticker = displayToTicker(d);
        const live = livePrices[d.symbol];
        if (live) {
          return {
            ...ticker,
            price: live.price,
            priceChangePct: live.change_pct ?? ticker.priceChangePct,
          };
        }
        return ticker;
      }),
    [filtered, livePrices],
  );

  function togglePhase(p: string) {
    setPhase((prev) => {
      const next = new Set(prev);
      next.has(p) ? next.delete(p) : next.add(p);
      return next;
    });
  }

  const selectedTicker = useMemo(() => {
    const found = tickers.find((t) => t.symbol === selectedSymbol);
    if (!found) return null;
    const ticker = displayToTicker(found);
    const live = livePrices[found.symbol];
    if (live) {
      return {
        ...ticker,
        price: live.price,
        priceChangePct: live.change_pct ?? ticker.priceChangePct,
      };
    }
    return ticker;
  }, [tickers, selectedSymbol, livePrices]);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "280px 1fr 260px",
        gap: 12,
        height: "100%",
      }}
    >
      {/* Status bar */}
      {!isConnected && !isLoading && (
        <div
          style={{
            gridColumn: "1 / -1",
            padding: "8px 16px",
            background: "rgba(255,61,90,0.08)",
            border: "1px solid rgba(255,61,90,0.2)",
            borderRadius: 8,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#FF3D5A",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "#FF3D5A",
            }}
          />
          Backend unreachable.{" "}
          <button
            onClick={() => {
              clearError();
              scan();
            }}
            style={{
              background: "rgba(255,61,90,0.15)",
              border: "1px solid rgba(255,61,90,0.3)",
              borderRadius: 4,
              color: "#FF3D5A",
              padding: "2px 8px",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
            }}
          >
            RETRY
          </button>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div
          style={{
            gridColumn: "1 / -1",
            padding: "8px 16px",
            background:
              error.kind === "auth"
                ? "rgba(155,89,182,0.08)"
                : error.retryable
                  ? "rgba(255,184,0,0.08)"
                  : "rgba(255,61,90,0.08)",
            border: `1px solid ${error.kind === "auth" ? "rgba(155,89,182,0.2)" : error.retryable ? "rgba(255,184,0,0.2)" : "rgba(255,61,90,0.2)"}`,
            borderRadius: 8,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color:
              error.kind === "auth"
                ? "#9B59B6"
                : error.retryable
                  ? "#FFB800"
                  : "#FF3D5A",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background:
                  error.kind === "auth"
                    ? "#9B59B6"
                    : error.retryable
                      ? "#FFB800"
                      : "#FF3D5A",
                flexShrink: 0,
              }}
            />
            <span>{error.message}</span>
          </div>
          <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
            {error.retryable && (
              <button
                onClick={retry}
                style={{
                  background: "rgba(255,184,0,0.15)",
                  border: "1px solid rgba(255,184,0,0.3)",
                  borderRadius: 4,
                  color: "#FFB800",
                  padding: "2px 8px",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                }}
              >
                RETRY
              </button>
            )}
            <button
              onClick={clearError}
              style={{
                background: "transparent",
                border: "none",
                color:
                  error.kind === "auth"
                    ? "#9B59B6"
                    : error.retryable
                      ? "#FFB800"
                      : "#FF3D5A",
                cursor: "pointer",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
              }}
            >
              dismiss
            </button>
          </div>
        </div>
      )}

      {/* Loading overlay */}
      {isLoading && (
        <div
          style={{
            gridColumn: "1 / -1",
            padding: 16,
            textAlign: "center",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "#4A5568",
          }}
        >
          Connecting to scanner backend...
        </div>
      )}

      {/* Modals for all tickers in universe */}
      {allTickers.map((t) => (
        <TickerModal
          key={`modal-${t.symbol}`}
          ticker={t}
          isVisible={t.symbol === selectedSymbol}
          onClose={() => setSelectedSymbol(null)}
        />
      ))}

      {/* ── Col 1: Universe Manager ── */}
      <StaggerContainer {...staggerContainerProps} style={{ height: "100%" }}>
        <StaggerCard {...staggerCardProps} style={{ height: "100%" }}>
          <div
            style={{
              ...PANEL,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            <SectionHeader>UNIVERSE MANAGER</SectionHeader>
            <UniverseManager
              tickers={tickers}
              universes={Object.keys(universes)}
              selectedUniverse={selectedUniverse}
              onSelectUniverse={setUniverse}
              onRescan={scan}
              isScanning={isScanning}
            />
          </div>
        </StaggerCard>
      </StaggerContainer>

      {/* ── Col 2: Results Grid ── */}
      <StaggerContainer
        {...staggerContainerProps}
        style={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {/* Filter + Sort bar */}
        <StaggerCard {...staggerCardProps}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
              padding: "10px 14px",
              background: "var(--bg-panel)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: "var(--radius-lg)",
            }}
          >
            {/* Phase filter pills */}
            <div style={{ display: "flex", gap: 4 }}>
              {PHASES.map((p) => {
                const active = phaseFilter.has(p);
                const color =
                  p === "A"
                    ? "#4A90D9"
                    : p === "B"
                      ? "#9B59B6"
                      : p === "C"
                        ? "#E67E22"
                        : "#2ECC71";
                return (
                  <button
                    key={p}
                    onClick={() => togglePhase(p)}
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      padding: "3px 10px",
                      background: active ? `${color}22` : "transparent",
                      border: `1px solid ${active ? color : "rgba(255,255,255,0.08)"}`,
                      borderRadius: "var(--radius-pill)",
                      color: active ? color : "#4A5568",
                      cursor: "pointer",
                      transition: "all 0.15s",
                      letterSpacing: "0.08em",
                    }}
                  >
                    {p}
                  </button>
                );
              })}
              {phaseFilter.size > 0 && (
                <button
                  onClick={() => setPhase(new Set())}
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    padding: "3px 8px",
                    background: "transparent",
                    border: "1px solid rgba(255,255,255,0.06)",
                    borderRadius: "var(--radius-pill)",
                    color: "#4A5568",
                    cursor: "pointer",
                  }}
                >
                  clear
                </button>
              )}
            </div>

            <div
              style={{
                width: 1,
                height: 20,
                background: "rgba(255,255,255,0.08)",
              }}
            />

            {/* Sort */}
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                background: "var(--bg-elevated)",
                border: "1px solid rgba(255,255,255,0.10)",
                borderRadius: 6,
                color: "#8B9AAF",
                padding: "4px 6px",
                outline: "none",
                cursor: "pointer",
              }}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>

            <button
              onClick={() => setSortAsc((a) => !a)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                background: "transparent",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: 4,
                color: "#8B9AAF",
                padding: "3px 8px",
                cursor: "pointer",
              }}
            >
              {sortAsc ? "ASC" : "DESC"}
            </button>

            <span
              style={{
                marginLeft: "auto",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
              }}
            >
              {isScanning ? "Scanning..." : `${filtered.length} tickers`}
            </span>
          </div>
        </StaggerCard>

        {/* Ticker rows */}
        <StaggerCard
          {...staggerCardProps}
          style={{ flex: 1, overflowY: "auto" }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {filteredTickers.length === 0 && !isLoading && (
              <div
                style={{
                  padding: 32,
                  textAlign: "center",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  color: "#4A5568",
                }}
              >
                {isScanning
                  ? "Scanning universe..."
                  : "No tickers match the active filters"}
              </div>
            )}
            {filteredTickers.map((t) => (
              <TickerRow
                key={t.symbol}
                ticker={t}
                onSelect={setSelectedSymbol}
              />
            ))}
          </div>
        </StaggerCard>
      </StaggerContainer>

      {/* ── Col 3: Strategy + Analytics ── */}
      <StaggerContainer
        {...staggerContainerProps}
        style={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <StaggerCard {...staggerCardProps}>
          <StrategyWeights />
        </StaggerCard>

        <StaggerCard {...staggerCardProps}>
          <div style={{ ...PANEL, overflowY: "auto" }}>
            <SectionHeader>PHASE ANALYTICS</SectionHeader>
            <div style={{ marginTop: 10 }}>
              <PhaseAnalytics tickers={tickers} />
            </div>
          </div>
        </StaggerCard>
      </StaggerContainer>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "#4A5568",
        letterSpacing: "0.12em",
      }}
    >
      {children}
    </div>
  );
}
