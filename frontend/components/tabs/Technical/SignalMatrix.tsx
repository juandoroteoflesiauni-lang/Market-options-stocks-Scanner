// @ts-nocheck
"use client";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import type { IndicatorState } from "./IndicatorPanel";
import { signalColor } from "@/utils/colors";

interface IndicatorSignal {
  key: string;
  label: string;
  direction: "BULL" | "BEAR" | "NEUTRAL";
  strength: number; // 0..1
  confluence: number; // 0..100
}

// Derive signals deterministically from the active indicator set
function deriveSignals(state: IndicatorState): IndicatorSignal[] {
  const MAP: Record<
    string,
    {
      label: string;
      dir: "BULL" | "BEAR" | "NEUTRAL";
      str: number;
      conf: number;
    }
  > = {
    ema20: { label: "EMA 20", dir: "BULL", str: 0.72, conf: 68 },
    ema50: { label: "EMA 50", dir: "BULL", str: 0.64, conf: 72 },
    ema200: { label: "EMA 200", dir: "BULL", str: 0.55, conf: 81 },
    vwap: { label: "VWAP", dir: "BULL", str: 0.6, conf: 65 },
    bb: { label: "Bollinger Bands", dir: "NEUTRAL", str: 0.42, conf: 51 },
    ichimoku: { label: "Ichimoku", dir: "BULL", str: 0.68, conf: 74 },
    rsi: { label: "RSI", dir: "BULL", str: 0.58, conf: 60 },
    macd: { label: "MACD", dir: "BULL", str: 0.65, conf: 67 },
    stoch: { label: "Stochastic", dir: "NEUTRAL", str: 0.4, conf: 48 },
    willr: { label: "Williams %R", dir: "BEAR", str: 0.52, conf: 55 },
    roc: { label: "ROC", dir: "BULL", str: 0.48, conf: 52 },
    cci: { label: "CCI", dir: "NEUTRAL", str: 0.35, conf: 44 },
    atr: { label: "ATR", dir: "NEUTRAL", str: 0.45, conf: 50 },
    kc: { label: "Keltner", dir: "BULL", str: 0.62, conf: 63 },
    hv: { label: "Hist. Vol", dir: "NEUTRAL", str: 0.38, conf: 42 },
    don: { label: "Donchian", dir: "BULL", str: 0.55, conf: 58 },
    obv: { label: "OBV", dir: "BULL", str: 0.7, conf: 70 },
    mfi: { label: "MFI", dir: "BULL", str: 0.61, conf: 64 },
    cvd: { label: "CVD", dir: "BEAR", str: 0.47, conf: 56 },
    optRsi: { label: "Options RSI", dir: "BULL", str: 0.75, conf: 78 },
    optMacd: { label: "Options MACD", dir: "BULL", str: 0.68, conf: 71 },
    gexBands: { label: "GEX Bands", dir: "BULL", str: 0.8, conf: 82 },
    deltaOsc: { label: "Delta Oscillator", dir: "BEAR", str: 0.55, conf: 59 },
    ivSqueeze: { label: "IV Squeeze", dir: "NEUTRAL", str: 0.44, conf: 49 },
    gammaRib: { label: "Gamma Ribbon", dir: "BULL", str: 0.72, conf: 76 },
    thetaClk: { label: "Theta Clock", dir: "BEAR", str: 0.48, conf: 54 },
  };

  return Object.entries(state)
    .filter(([, on]) => on)
    .map(([key]) => {
      const def = MAP[key];
      if (!def) return null;
      return { key, ...def, strength: def.str, confluence: def.conf };
    })
    .filter(Boolean) as IndicatorSignal[];
}

function compositeSignal(signals: IndicatorSignal[]): {
  dir: "BULL" | "BEAR" | "NEUTRAL";
  score: number;
} {
  if (signals.length === 0) return { dir: "NEUTRAL", score: 0 };
  let bullW = 0,
    bearW = 0,
    totalW = 0;
  for (const s of signals) {
    const w = s.confluence / 100;
    totalW += w;
    if (s.direction === "BULL") bullW += w;
    else if (s.direction === "BEAR") bearW += w;
  }
  const bullPct = bullW / totalW;
  const bearPct = bearW / totalW;
  const score = Math.round((bullPct - bearPct) * 100);
  const dir = bullPct > 0.55 ? "BULL" : bearPct > 0.45 ? "BEAR" : "NEUTRAL";
  return { dir, score };
}

interface Props {
  indicators: IndicatorState;
}

export function SignalMatrix({ indicators }: Props) {
  const signals = deriveSignals(indicators);
  const composite = compositeSignal(signals);

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>Signal Matrix</span>
        <span style={{ color: "#E8EDF5" }}>{signals.length} active</span>
      </div>

      {/* Rows */}
      <div style={{ overflowY: "auto", flex: 1, maxHeight: 260 }}>
        {signals.length === 0 ? (
          <div
            style={{
              padding: 20,
              textAlign: "center",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#4A5568",
            }}
          >
            No indicators active
          </div>
        ) : (
          signals.map((s) => {
            const color = signalColor(s.direction);
            const Icon =
              s.direction === "BULL"
                ? TrendingUp
                : s.direction === "BEAR"
                  ? TrendingDown
                  : Minus;
            return (
              <div
                key={s.key}
                style={{
                  padding: "6px 12px",
                  borderBottom: "1px solid rgba(255,255,255,0.03)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "#E8EDF5",
                    }}
                  >
                    {s.label}
                  </span>
                  <div
                    style={{ display: "flex", alignItems: "center", gap: 4 }}
                  >
                    <Icon size={10} color={color} />
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                        color,
                      }}
                    >
                      {s.direction}
                    </span>
                  </div>
                </div>
                {/* Strength bar */}
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div
                    style={{
                      flex: 1,
                      height: 3,
                      background: "var(--bg-hover)",
                      borderRadius: 2,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        height: "100%",
                        width: `${s.strength * 100}%`,
                        background: color,
                        borderRadius: 2,
                        boxShadow: `0 0 4px ${color}66`,
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      color: "#4A5568",
                      minWidth: 28,
                    }}
                  >
                    {s.confluence}%
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Composite footer */}
      <div
        style={{
          padding: "8px 12px",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.1em",
          }}
        >
          COMPOSITE
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {composite.dir === "BULL" ? (
            <TrendingUp size={13} color={signalColor("BULL")} />
          ) : composite.dir === "BEAR" ? (
            <TrendingDown size={13} color={signalColor("BEAR")} />
          ) : (
            <Minus size={13} color={signalColor("NEUTRAL")} />
          )}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 700,
              color: signalColor(composite.dir),
            }}
          >
            {composite.dir}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#4A5568",
            }}
          >
            ({composite.score > 0 ? "+" : ""}
            {composite.score})
          </span>
        </div>
      </div>
    </div>
  );
}
