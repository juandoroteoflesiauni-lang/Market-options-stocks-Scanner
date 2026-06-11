"use client";
import { X } from "lucide-react";
import { phaseColor } from "@/utils/colors";

interface Props {
  label: string;
  phase?: "A" | "B" | "C" | "D";
  onRemove?: () => void;
  onClick?: () => void;
  active?: boolean;
}

export function Chip({ label, phase, onRemove, onClick, active }: Props) {
  const accentColor = phase ? phaseColor(phase) : "#00C3FF";
  const isInteractive = !!onClick;

  return (
    <span
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: active ? accentColor : "#8B9AAF",
        background: active ? `${accentColor}14` : "rgba(255,255,255,0.04)",
        border: `1px solid ${active ? `${accentColor}44` : "rgba(255,255,255,0.08)"}`,
        borderRadius: "var(--radius-pill)",
        padding: "3px 8px",
        letterSpacing: "0.06em",
        cursor: isInteractive || onRemove ? "pointer" : "default",
        transition: "all 0.15s ease",
        whiteSpace: "nowrap",
      }}
    >
      {phase && (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: phaseColor(phase),
            display: "inline-block",
            flexShrink: 0,
          }}
        />
      )}
      {label}
      {onRemove && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          style={{
            display: "flex",
            alignItems: "center",
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
            color: "#4A5568",
            lineHeight: 1,
          }}
        >
          <X size={10} />
        </button>
      )}
    </span>
  );
}
