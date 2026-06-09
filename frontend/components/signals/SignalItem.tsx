import { Badge } from "@/components/ui/Badge";

import type {
  ExecutionSignal,
  SignalType,
  SignalStrength,
} from "@/store/types";

interface SignalItemProps {
  signal: ExecutionSignal;
}

const signalVariant: Record<SignalType, "buy" | "sell" | "neutral"> = {
  BUY: "buy",
  SELL: "sell",
  NEUTRAL: "neutral",
};

const strengthDot: Record<SignalStrength, string> = {
  CRITICAL: "bg-signal-sell animate-pulse",
  HIGH: "bg-signal-warning",
  MEDIUM: "bg-signal-neutral",
  LOW: "bg-text-muted",
};

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

export function SignalItem({ signal }: SignalItemProps) {
  return (
    <div
      className={[
        "flex items-center justify-between",
        "px-4 py-3 rounded-lg",
        "border border-border-subtle",
        "bg-bg-surface/50",
        "hover:bg-bg-elevated/50 transition-colors duration-150",
      ].join(" ")}
    >
      <div className="flex items-center gap-3">
        {/* Strength dot */}
        <div
          className={[
            "h-2 w-2 rounded-full",
            strengthDot[signal.strength],
          ].join(" ")}
          title={signal.strength}
        />

        {/* Ticker */}
        <span className="text-sm font-mono font-semibold tracking-wide uppercase text-text-primary">
          {signal.ticker}
        </span>

        {/* Signal type badge */}
        <Badge variant={signalVariant[signal.signal_type]}>
          {signal.signal_type}
        </Badge>
      </div>

      <div className="flex items-center gap-4">
        {/* Price at signal */}
        <span className="font-mono tabular-nums text-sm text-text-primary">
          ${signal.price_at_signal}
        </span>

        {/* Relative timestamp */}
        <span className="text-xs text-text-muted min-w-[4rem] text-right">
          {formatRelativeTime(signal.emitted_at)}
        </span>
      </div>
    </div>
  );
}
