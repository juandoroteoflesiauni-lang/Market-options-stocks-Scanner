"use client";
import { riskBarColor } from "@/utils/colors";

interface Props {
  value: number; // 0..1
  warn?: number; // default 0.6
  danger?: number; // default 0.8
  label?: string;
  showValue?: boolean;
  height?: number;
}

export function RiskBar({
  value,
  warn = 0.6,
  danger = 0.8,
  label,
  showValue = true,
  height = 4,
}: Props) {
  const clamped = Math.max(0, Math.min(1, value));
  const color = riskBarColor(clamped, warn, danger);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {(label || showValue) && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          {label && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#8B9AAF",
                letterSpacing: "0.08em",
              }}
            >
              {label}
            </span>
          )}
          {showValue && (
            <span
              style={{ fontFamily: "var(--font-mono)", fontSize: 10, color }}
            >
              {Math.round(clamped * 100)}%
            </span>
          )}
        </div>
      )}
      <div
        style={{
          width: "100%",
          height,
          background: "var(--bg-hover)",
          borderRadius: "var(--radius-pill)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${clamped * 100}%`,
            background: color,
            borderRadius: "var(--radius-pill)",
            transition: "width 0.4s ease, background 0.3s ease",
            boxShadow: `0 0 6px ${color}66`,
          }}
        />
      </div>
    </div>
  );
}
