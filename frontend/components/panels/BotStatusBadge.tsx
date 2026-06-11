"use client";
type Status = "RUNNING" | "PAUSED" | "ERROR" | "IDLE";

interface Props {
  status: Status;
}

const CONFIG: Record<
  Status,
  { color: string; animation: string; label: string }
> = {
  RUNNING: {
    color: "#00E676",
    animation: "pulse-green 2s ease-in-out infinite",
    label: "RUNNING",
  },
  PAUSED: { color: "#FFB800", animation: "none", label: "PAUSED" },
  ERROR: {
    color: "#FF3D5A",
    animation: "strobe-red 0.8s ease-in-out infinite",
    label: "ERROR",
  },
  IDLE: { color: "#4A5568", animation: "none", label: "IDLE" },
};

export function BotStatusBadge({ status }: Props) {
  const { color, animation, label } = CONFIG[status];

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color,
        background: `${color}14`,
        border: `1px solid ${color}44`,
        borderRadius: "var(--radius-pill)",
        padding: "3px 10px",
        letterSpacing: "0.1em",
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: color,
          display: "inline-block",
          flexShrink: 0,
          animation,
        }}
      />
      {label}
    </span>
  );
}
