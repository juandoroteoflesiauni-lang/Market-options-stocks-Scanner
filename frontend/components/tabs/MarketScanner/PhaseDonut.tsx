"use client";
import { useMemo } from "react";
import type { Ticker } from "@/types";
import { phaseColor, phaseLabel } from "@/utils/colors";

interface Props {
  tickers: Ticker[];
  size?: number;
}

const PHASES = ["A", "B", "C", "D"] as const;

export function PhaseDonut({ tickers, size = 120 }: Props) {
  const { slices, counts } = useMemo(() => {
    const counts = Object.fromEntries(PHASES.map((p) => [p, 0])) as Record<
      string,
      number
    >;
    tickers.forEach((t) => counts[t.phase]++);
    const total = tickers.length || 1;

    let angle = -Math.PI / 2;
    const slices = PHASES.map((p) => {
      const frac = counts[p] / total;
      const start = angle;
      const end = angle + frac * 2 * Math.PI;
      angle = end;
      return {
        phase: p,
        frac,
        start,
        end,
        color: phaseColor(p),
        count: counts[p],
      };
    }).filter((s) => s.frac > 0);

    return { slices, counts };
  }, [tickers]);

  const cx = size / 2;
  const cy = size / 2;
  const R = size * 0.42;
  const r = size * 0.26;

  function arc(s: number, e: number, outerR: number, innerR: number) {
    if (Math.abs(e - s) >= 2 * Math.PI - 0.001) {
      // Full circle — draw as two arcs
      return [
        `M ${cx + outerR} ${cy}`,
        `A ${outerR} ${outerR} 0 1 1 ${cx - outerR} ${cy}`,
        `A ${outerR} ${outerR} 0 1 1 ${cx + outerR} ${cy}`,
        `M ${cx + innerR} ${cy}`,
        `A ${innerR} ${innerR} 0 1 0 ${cx - innerR} ${cy}`,
        `A ${innerR} ${innerR} 0 1 0 ${cx + innerR} ${cy}`,
        "Z",
      ].join(" ");
    }
    const large = e - s > Math.PI ? 1 : 0;
    const x1 = cx + outerR * Math.cos(s);
    const y1 = cy + outerR * Math.sin(s);
    const x2 = cx + outerR * Math.cos(e);
    const y2 = cy + outerR * Math.sin(e);
    const x3 = cx + innerR * Math.cos(e);
    const y3 = cy + innerR * Math.sin(e);
    const x4 = cx + innerR * Math.cos(s);
    const y4 = cy + innerR * Math.sin(s);
    return `M ${x1} ${y1} A ${outerR} ${outerR} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${innerR} ${innerR} 0 ${large} 0 ${x4} ${y4} Z`;
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <svg width={size} height={size}>
        {slices.map((s) => (
          <path
            key={s.phase}
            d={arc(s.start, s.end, R, r)}
            fill={s.color}
            opacity={0.85}
            stroke="var(--bg-panel)"
            strokeWidth={2}
          />
        ))}
        {/* Center label */}
        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          fill="#E8EDF5"
          fontSize={size * 0.14}
          fontFamily="var(--font-mono)"
          fontWeight="bold"
        >
          {tickers.length}
        </text>
        <text
          x={cx}
          y={cy + 10}
          textAnchor="middle"
          fill="#4A5568"
          fontSize={size * 0.09}
          fontFamily="var(--font-mono)"
        >
          TICKERS
        </text>
      </svg>

      {/* Legend */}
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {PHASES.map((p) => (
          <div
            key={p}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: phaseColor(p),
                display: "inline-block",
                flexShrink: 0,
              }}
            />
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#8B9AAF",
              }}
            >
              {p} — {phaseLabel(p).slice(0, 8)}
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: phaseColor(p),
                marginLeft: "auto",
              }}
            >
              {counts[p]}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
