"use client";
import { useState, useRef } from "react";
import { Plus, Search } from "lucide-react";
import { Chip } from "@/components/ui/Chip";
import { TickerLogo } from "@/components/panels/TickerLogo";
import { PhaseDonut } from "./PhaseDonut";
import { displayListToTickers } from "@/services/scannerService";
import type { ScannerTickerDisplay } from "@/types/marketScanner";

interface Props {
  tickers: ScannerTickerDisplay[];
  universes: string[];
  selectedUniverse: string;
  onSelectUniverse: (name: string) => void;
  onRescan: () => void;
  isScanning: boolean;
}

export function UniverseManager({
  tickers,
  universes,
  selectedUniverse,
  onSelectUniverse,
  onRescan,
  isScanning,
}: Props) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const symbols = tickers.map((t) => t.symbol);
  const suggestions =
    query.length >= 1
      ? symbols
          .filter((s) => s.toLowerCase().includes(query.toLowerCase()))
          .slice(0, 6)
      : [];

  const topMovers = [...tickers]
    .sort(
      (a, b) =>
        Math.abs(parseFloat(b.change_pct)) - Math.abs(parseFloat(a.change_pct)),
    )
    .slice(0, 5);

  const donutTickers = displayListToTickers(tickers);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Universe Selector */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <label
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.1em",
          }}
        >
          UNIVERSE
        </label>
        <div style={{ display: "flex", gap: 6 }}>
          <select
            value={selectedUniverse}
            onChange={(e) => onSelectUniverse(e.target.value)}
            style={{
              flex: 1,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              background: "var(--bg-elevated)",
              border: "1px solid rgba(255,255,255,0.10)",
              borderRadius: 6,
              color: "#E8EDF5",
              padding: "6px 8px",
              cursor: "pointer",
              outline: "none",
            }}
          >
            {universes.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
          <button
            onClick={onRescan}
            disabled={isScanning}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "0 10px",
              background: isScanning
                ? "rgba(255,255,255,0.05)"
                : "rgba(0,195,255,0.1)",
              border: `1px solid ${isScanning ? "rgba(255,255,255,0.08)" : "rgba(0,195,255,0.3)"}`,
              borderRadius: 6,
              color: isScanning ? "#4A5568" : "#00C3FF",
              cursor: isScanning ? "default" : "pointer",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
            }}
          >
            <Plus size={12} /> {isScanning ? "SCANNING..." : "SCAN MARKET"}
          </button>
        </div>
      </div>

      {/* Search / Add input */}
      <div style={{ position: "relative" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "7px 10px",
            background: "var(--bg-elevated)",
            border: "1px solid rgba(255,255,255,0.10)",
            borderRadius: 6,
          }}
        >
          <Search size={12} color="#4A5568" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value.toUpperCase())}
            placeholder="Search ticker..."
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#E8EDF5",
            }}
          />
        </div>

        {/* Autocomplete dropdown */}
        {suggestions.length > 0 && (
          <div
            style={{
              position: "absolute",
              top: "100%",
              left: 0,
              right: 0,
              zIndex: 50,
              background: "var(--bg-elevated)",
              border: "1px solid rgba(0,195,255,0.2)",
              borderRadius: 6,
              marginTop: 2,
              overflow: "hidden",
            }}
          >
            {suggestions.map((sym) => {
              const t = tickers.find((x) => x.symbol === sym);
              return (
                <div
                  key={sym}
                  onClick={() => {
                    setQuery("");
                    inputRef.current?.focus();
                  }}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "7px 12px",
                    cursor: "pointer",
                    borderBottom: "1px solid rgba(255,255,255,0.04)",
                    transition: "background 0.1s",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = "transparent")
                  }
                >
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: "#00C3FF",
                    }}
                  >
                    {sym}
                  </span>
                  {t && (
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                        color: "#8B9AAF",
                      }}
                    >
                      ${t.price}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Chip grid */}
      {tickers.length > 0 && (
        <div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 6,
            }}
          >
            UNIVERSE ({tickers.length})
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {tickers.map((t) => (
              <Chip
                key={t.symbol}
                label={t.symbol}
                phase={t.phase}
                onRemove={() => {}}
              />
            ))}
          </div>
        </div>
      )}

      {/* Phase Donut */}
      <div>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.1em",
            marginBottom: 8,
          }}
        >
          PHASE DISTRIBUTION
        </div>
        <PhaseDonut tickers={donutTickers} size={110} />
      </div>

      {/* Top Movers */}
      {topMovers.length > 0 && (
        <div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 6,
            }}
          >
            TOP MOVERS
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {topMovers.map((t) => {
              const chg = parseFloat(t.change_pct);
              const up = chg >= 0;
              return (
                <div
                  key={t.symbol}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "4px 8px",
                    background: "rgba(255,255,255,0.02)",
                    borderRadius: 4,
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      width: 72,
                    }}
                  >
                    <TickerLogo symbol={t.symbol} size={14} />
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "#00C3FF",
                      }}
                    >
                      {t.symbol}
                    </span>
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: "#8B9AAF",
                    }}
                  >
                    ${t.price}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: up ? "#00E676" : "#FF3D5A",
                    }}
                  >
                    {up ? "+" : ""}
                    {chg.toFixed(2)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
