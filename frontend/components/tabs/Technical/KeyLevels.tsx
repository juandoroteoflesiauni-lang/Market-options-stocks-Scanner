"use client";
import type { OHLCV } from "@/types";
import type { OptionsChainRow } from "@/types";

interface Level {
  label: string;
  type: "support" | "resistance" | "pivot" | "options";
  value: number;
  distancePct: number;
}

function buildLevels(
  candles: OHLCV[],
  chain: OptionsChainRow[],
  spot: number,
): Level[] {
  const closes = candles.map((c) => c.close);
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);

  const resistance = Math.max(...highs);
  const support = Math.min(...lows);

  // VWAP
  const cumVP = candles.reduce((s, c) => s + c.close * c.volume, 0);
  const cumV = candles.reduce((s, c) => s + c.volume, 0);
  const vwap = cumVP / Math.max(1, cumV);

  // Weekly open = first candle close (approximation)
  const weeklyOpen = closes[0];

  // Daily open = close of last 8 candles ago (approx)
  const dailyOpen = closes[Math.max(0, closes.length - 8)];

  // Options chain levels
  const callWall = chain.reduce(
    (best, r) =>
      r.call.oi > best.oi ? { strike: r.strike, oi: r.call.oi } : best,
    { strike: 0, oi: 0 },
  ).strike;
  const putWall = chain.reduce(
    (best, r) =>
      r.put.oi > best.oi ? { strike: r.strike, oi: r.put.oi } : best,
    { strike: 0, oi: 0 },
  ).strike;

  // Max Pain
  const maxPain = chain.reduce(
    (best, row) => {
      const pain = chain.reduce(
        (s, r) =>
          s +
          Math.max(0, row.strike - r.strike) * r.call.oi +
          Math.max(0, r.strike - row.strike) * r.put.oi,
        0,
      );
      return pain < best.pain ? { strike: row.strike, pain } : best;
    },
    { strike: 0, pain: Infinity },
  ).strike;

  // GEX Flip — strike where net gamma changes sign (approximate: zero-cross in sorted chain)
  const sortedByStrike = [...chain].sort((a, b) => a.strike - b.strike);
  let gexFlipStrike = spot;
  for (let i = 0; i < sortedByStrike.length - 1; i++) {
    const gex1 =
      sortedByStrike[i].call.gamma * sortedByStrike[i].call.oi -
      sortedByStrike[i].put.gamma * sortedByStrike[i].put.oi;
    const gex2 =
      sortedByStrike[i + 1].call.gamma * sortedByStrike[i + 1].call.oi -
      sortedByStrike[i + 1].put.gamma * sortedByStrike[i + 1].put.oi;
    if (gex1 < 0 !== gex2 < 0) {
      gexFlipStrike =
        (sortedByStrike[i].strike + sortedByStrike[i + 1].strike) / 2;
      break;
    }
  }

  const raw: Array<{ label: string; type: Level["type"]; value: number }> = [
    { label: "Resistance", type: "resistance", value: resistance },
    { label: "Call Wall", type: "options", value: callWall },
    { label: "VWAP", type: "pivot", value: vwap },
    { label: "GEX Flip", type: "pivot", value: gexFlipStrike },
    { label: "Max Pain", type: "options", value: maxPain },
    { label: "Weekly Open", type: "pivot", value: weeklyOpen },
    { label: "Daily Open", type: "pivot", value: dailyOpen },
    { label: "Put Wall", type: "options", value: putWall },
    { label: "Support", type: "support", value: support },
  ];

  return raw
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
    .map((r) => ({
      ...r,
      distancePct: ((r.value - spot) / spot) * 100,
    }));
}

function levelColor(type: Level["type"]): string {
  switch (type) {
    case "resistance":
      return "#FF3D5A";
    case "support":
      return "#00E676";
    case "options":
      return "#FFB800";
    default:
      return "#00C3FF";
  }
}

function levelBg(type: Level["type"]): string {
  return levelColor(type) + "18";
}

interface Props {
  candles: OHLCV[];
  chain: OptionsChainRow[];
  spot: number;
}

export function KeyLevels({ candles, chain, spot }: Props) {
  const levels = buildLevels(candles, chain, spot);

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
        }}
      >
        <span>Key Levels</span>
        <span style={{ color: "#00C3FF" }}>${spot.toFixed(2)}</span>
      </div>

      <div style={{ overflowY: "auto", flex: 1 }}>
        {levels.map((lv) => {
          const color = levelColor(lv.type);
          const above = lv.distancePct >= 0;
          return (
            <div
              key={lv.label}
              style={{
                display: "flex",
                alignItems: "center",
                padding: "6px 12px",
                borderBottom: "1px solid rgba(255,255,255,0.03)",
                gap: 8,
                background:
                  lv.distancePct === 0 ? "var(--bg-hover)" : "transparent",
              }}
            >
              {/* Type chip */}
              <div
                style={{
                  width: 3,
                  height: 20,
                  background: color,
                  borderRadius: 2,
                  flexShrink: 0,
                  boxShadow: `0 0 4px ${color}66`,
                }}
              />
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "#8B9AAF",
                  flex: 1,
                  whiteSpace: "nowrap",
                }}
              >
                {lv.label}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "#E8EDF5",
                  fontWeight: 600,
                }}
              >
                ${lv.value.toFixed(2)}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: above ? "#00E676" : "#FF3D5A",
                  minWidth: 48,
                  textAlign: "right",
                }}
              >
                {above ? "+" : ""}
                {lv.distancePct.toFixed(2)}%
              </span>
            </div>
          );
        })}
      </div>

      {/* Spot marker */}
      <div
        style={{
          padding: "6px 12px",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--bg-elevated)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
          }}
        >
          Spot
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "#00C3FF",
            fontWeight: 700,
          }}
        >
          ${spot.toFixed(2)}
        </span>
      </div>
    </div>
  );
}
