"use client";

import { cn } from "@/lib/utils";

interface BingxModeSwitchProps {
  isLive: boolean;
  onToggle: (live: boolean) => void;
  liveReady?: boolean;
}

export function BingxModeSwitch({
  isLive,
  onToggle,
  liveReady = false,
}: BingxModeSwitchProps) {
  const liveDisabled = !liveReady && !isLive;
  return (
    <div className="flex border border-line bg-base font-mono text-[11px] font-bold uppercase tracking-[0.08em]">
      <button
        type="button"
        onClick={() => onToggle(false)}
        className={cn(
          "h-9 px-3 transition-colors",
          !isLive
            ? "bg-info/15 text-info"
            : "bg-transparent text-ink-500 hover:bg-hover hover:text-ink-100",
        )}
      >
        DRY RUN
      </button>
      <button
        type="button"
        onClick={() => !liveDisabled && onToggle(true)}
        disabled={liveDisabled}
        title={
          liveDisabled
            ? "Preflight no completado — ejecuta /healthcheck?probe=true"
            : undefined
        }
        className={cn(
          "h-9 border-l border-line px-3 transition-colors",
          isLive
            ? "bg-bear/15 text-bear"
            : liveDisabled
              ? "cursor-not-allowed text-ink-600 opacity-50"
              : "bg-transparent text-ink-500 hover:bg-hover hover:text-ink-100",
        )}
      >
        LIVE
      </button>
    </div>
  );
}
