"use client";
import type { ScannerTickerDisplay } from "@/types/marketScanner";
import { phaseColor, phaseLabel } from "@/utils/colors";

const PHASES = ["A", "B", "C", "D"] as const;

interface Props {
  tickers: ScannerTickerDisplay[];
}

export function PhaseAnalytics({ tickers }: Props) {
  const total = tickers.length || 1;

  const stats = PHASES.map((p) => {
    const group = tickers.filter((t) => t.phase === p);
    const avgScore = group.length
      ? group.reduce((s, t) => s + (parseFloat(t.scanner_score) || 0), 0) /
        group.length
      : 0;
    const avgIntraday = group.length
      ? group.reduce((s, t) => s + (parseFloat(t.intraday_score) || 0), 0) /
        group.length
      : 0;
    return {
      phase: p,
      count: group.length,
      pct: (group.length / total) * 100,
      avgScore,
      avgIntraday,
    };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {stats.map((s) => {
        const color = phaseColor(s.phase);
        return (
          <div
            key={s.phase}
            style={{
              padding: "8px 10px",
              background: "rgba(255,255,255,0.02)",
              border: `1px solid ${color}20`,
              borderRadius: 6,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 5,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: color,
                    display: "inline-block",
                  }}
                />
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    color,
                    letterSpacing: "0.06em",
                  }}
                >
                  {s.phase} — {phaseLabel(s.phase)}
                </span>
              </div>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  fontWeight: 600,
                  color,
                }}
              >
                {s.count}
              </span>
            </div>

            {/* Mini progress bar */}
            <div
              style={{
                height: 2,
                background: "rgba(255,255,255,0.06)",
                borderRadius: 1,
                marginBottom: 5,
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${s.pct}%`,
                  background: color,
                  borderRadius: 1,
                  transition: "width 0.4s ease",
                }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <Stat label="PCT" value={`${s.pct.toFixed(0)}%`} />
              <Stat label="AVG SCORE" value={s.avgScore.toFixed(1)} />
              <Stat
                label="AVG INTRADAY"
                value={s.avgIntraday.toFixed(0)}
                color={s.avgIntraday >= 0 ? "#00E676" : "#FF3D5A"}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Stat({
  label,
  value,
  color = "#8B9AAF",
}: {
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 8,
          color: "#4A5568",
          letterSpacing: "0.1em",
        }}
      >
        {label}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color }}>
        {value}
      </span>
    </div>
  );
}
