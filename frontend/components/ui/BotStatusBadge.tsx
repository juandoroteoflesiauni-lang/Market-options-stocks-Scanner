"use client";
import clsx from "clsx";

export type BotStatus = "RUNNING" | "PAUSED" | "ERROR" | "IDLE";

export function BotStatusBadge({ status }: { status: BotStatus }) {
  const statusConfig: Record<
    BotStatus,
    { color: string; label: string; pulse: boolean }
  > = {
    RUNNING: {
      color: "bg-signal-bull text-signal-bull",
      label: "RUNNING",
      pulse: true,
    },
    PAUSED: {
      color: "bg-signal-warn text-signal-warn",
      label: "PAUSED",
      pulse: false,
    },
    ERROR: {
      color: "bg-signal-bear text-signal-bear",
      label: "ERROR",
      pulse: true,
    },
    IDLE: {
      color: "bg-signal-neutral text-signal-neutral",
      label: "IDLE",
      pulse: false,
    },
  };

  const config = statusConfig[status];

  return (
    <div className="flex items-center gap-2 bg-bg-panel px-2 py-1 rounded-md border border-border-subtle">
      <div className="relative flex h-2 w-2">
        {config.pulse && (
          <span
            className={clsx(
              "animate-ping absolute inline-flex h-full w-full rounded-full opacity-75",
              config.color.split(" ")[0], // Gets the bg color class
            )}
          />
        )}
        <span
          className={clsx(
            "relative inline-flex rounded-full h-2 w-2",
            config.color.split(" ")[0],
          )}
        />
      </div>
      <span
        className={clsx(
          "font-mono text-[10px] uppercase",
          config.color.split(" ")[1],
        )}
      >
        {config.label}
      </span>
    </div>
  );
}
