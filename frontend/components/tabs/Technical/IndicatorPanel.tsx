"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export interface IndicatorState {
  [key: string]: boolean;
}

interface GroupDef {
  label: string;
  color: string;
  indicators: Array<{ key: string; label: string }>;
}

const GROUPS: GroupDef[] = [
  {
    label: "Trend",
    color: "#00C3FF",
    indicators: [
      { key: "ema20", label: "EMA 20" },
      { key: "ema50", label: "EMA 50" },
      { key: "ema200", label: "EMA 200" },
      { key: "vwap", label: "VWAP" },
      { key: "bb", label: "Bollinger Bands" },
      { key: "ichimoku", label: "Ichimoku Cloud" },
    ],
  },
  {
    label: "Momentum",
    color: "#FFB800",
    indicators: [
      { key: "rsi", label: "RSI" },
      { key: "macd", label: "MACD" },
      { key: "stoch", label: "Stochastic" },
      { key: "willr", label: "Williams %R" },
      { key: "roc", label: "Rate of Change" },
      { key: "cci", label: "CCI" },
    ],
  },
  {
    label: "Volatility",
    color: "#8B5CF6",
    indicators: [
      { key: "atr", label: "ATR" },
      { key: "kc", label: "Keltner Channel" },
      { key: "hv", label: "Hist. Volatility" },
      { key: "don", label: "Donchian" },
    ],
  },
  {
    label: "Volume",
    color: "#10B981",
    indicators: [
      { key: "obv", label: "OBV" },
      { key: "mfi", label: "MFI" },
      { key: "cvd", label: "CVD" },
    ],
  },
  {
    label: "Options-Reformulated",
    color: "#FF3D5A",
    indicators: [
      { key: "optRsi", label: "Options RSI" },
      { key: "optMacd", label: "Options MACD" },
      { key: "gexBands", label: "GEX Bands" },
      { key: "deltaOsc", label: "Delta Oscillator" },
      { key: "ivSqueeze", label: "IV Squeeze" },
      { key: "gammaRib", label: "Gamma Ribbon" },
      { key: "thetaClk", label: "Theta Clock" },
    ],
  },
];

const DEFAULT_STATE: IndicatorState = {
  ema20: true,
  ema50: true,
  ema200: false,
  vwap: true,
  bb: false,
  ichimoku: false,
  rsi: true,
  macd: false,
  stoch: false,
  willr: false,
  roc: false,
  cci: false,
  atr: false,
  kc: false,
  hv: false,
  don: false,
  obv: false,
  mfi: false,
  cvd: false,
  optRsi: true,
  optMacd: false,
  gexBands: true,
  deltaOsc: false,
  ivSqueeze: false,
  gammaRib: false,
  thetaClk: false,
};

interface Props {
  state: IndicatorState;
  onChange: (next: IndicatorState) => void;
}

export { DEFAULT_STATE };

export function IndicatorPanel({ state, onChange }: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  function toggle(key: string) {
    onChange({ ...state, [key]: !state[key] });
  }

  function toggleGroup(label: string) {
    setCollapsed((c) => ({ ...c, [label]: !c[label] }));
  }

  function activeCount(g: GroupDef) {
    return g.indicators.filter((ind) => state[ind.key]).length;
  }

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
          padding: "10px 12px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        Indicators
      </div>

      <div style={{ overflowY: "auto", flex: 1 }}>
        {GROUPS.map((g) => {
          const isOpen = !collapsed[g.label];
          const cnt = activeCount(g);
          return (
            <div
              key={g.label}
              style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}
            >
              <button
                onClick={() => toggleGroup(g.label)}
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "8px 12px",
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: g.color,
                }}
              >
                {isOpen ? (
                  <ChevronDown size={12} color={g.color} />
                ) : (
                  <ChevronRight size={12} color={g.color} />
                )}
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    flex: 1,
                    textAlign: "left",
                  }}
                >
                  {g.label}
                </span>
                {cnt > 0 && (
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      color: g.color,
                      background: `${g.color}20`,
                      border: `1px solid ${g.color}40`,
                      borderRadius: 3,
                      padding: "0 5px",
                    }}
                  >
                    {cnt}
                  </span>
                )}
              </button>

              {isOpen && (
                <div style={{ paddingBottom: 6 }}>
                  {g.indicators.map((ind) => {
                    const on = !!state[ind.key];
                    return (
                      <div
                        key={ind.key}
                        onClick={() => toggle(ind.key)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "5px 12px 5px 20px",
                          cursor: "pointer",
                          transition: "background 0.1s",
                        }}
                        onMouseEnter={(e) => {
                          (e.currentTarget as HTMLElement).style.background =
                            "var(--bg-hover)";
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLElement).style.background =
                            "transparent";
                        }}
                      >
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            color: on ? "#E8EDF5" : "#4A5568",
                            transition: "color 0.15s",
                          }}
                        >
                          {ind.label}
                        </span>
                        {/* Mini toggle */}
                        <div
                          style={{
                            width: 28,
                            height: 14,
                            background: on ? g.color : "var(--bg-hover)",
                            borderRadius: 7,
                            position: "relative",
                            transition: "background 0.2s",
                            flexShrink: 0,
                          }}
                        >
                          <div
                            style={{
                              width: 10,
                              height: 10,
                              background: on ? "#fff" : "#4A5568",
                              borderRadius: "50%",
                              position: "absolute",
                              top: 2,
                              left: on ? 16 : 2,
                              transition: "left 0.2s, background 0.2s",
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
