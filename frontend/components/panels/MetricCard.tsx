"use client";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { MiniSparkline } from "./MiniSparkline";
import { formatPct } from "@/utils/format";

interface Props {
  title: string;
  value: string | number;
  delta?: number;
  deltaLabel?: string;
  sparkline?: number[];
  unit?: string;
  accentColor?: string;
}

export function MetricCard({
  title,
  value,
  delta,
  deltaLabel,
  sparkline,
  unit,
  accentColor = "#00C3FF",
}: Props) {
  const hasDelta = delta !== undefined;
  const deltaPositive = (delta ?? 0) >= 0;
  const deltaColor = hasDelta
    ? delta === 0
      ? "#8B9AAF"
      : deltaPositive
        ? "#00E676"
        : "#FF3D5A"
    : "#8B9AAF";

  const DeltaIcon =
    !hasDelta || delta === 0
      ? Minus
      : deltaPositive
        ? TrendingUp
        : TrendingDown;

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        minWidth: 120,
      }}
    >
      {/* Title */}
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        {title}
      </span>

      {/* Value row */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 3 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 20,
              fontWeight: 700,
              color: "#E8EDF5",
              lineHeight: 1,
            }}
          >
            {value}
          </span>
          {unit && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "#8B9AAF",
              }}
            >
              {unit}
            </span>
          )}
        </div>

        {sparkline && sparkline.length > 1 && (
          <MiniSparkline data={sparkline} width={40} height={20} />
        )}
      </div>

      {/* Delta */}
      {hasDelta && (
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <DeltaIcon size={11} color={deltaColor} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: deltaColor,
            }}
          >
            {formatPct(delta!)}
          </span>
          {deltaLabel && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
              }}
            >
              {deltaLabel}
            </span>
          )}
        </div>
      )}

      {/* Accent line at bottom */}
      <div
        style={{
          height: 1,
          background: `linear-gradient(90deg, ${accentColor}44, transparent)`,
          marginTop: 2,
        }}
      />
    </div>
  );
}
