interface ProgressBarProps {
  value: number;
  max: number;
  variant?: "default" | "buy" | "sell" | "neutral";
  showLabel?: boolean;
}

const barColors: Record<string, string> = {
  default: "bg-chart-1",
  buy: "bg-signal-buy",
  sell: "bg-signal-sell",
  neutral: "bg-signal-neutral",
};

const glowColors: Record<string, string> = {
  default: "shadow-chart-1/30",
  buy: "shadow-signal-buy/30",
  sell: "shadow-signal-sell/30",
  neutral: "shadow-signal-neutral/30",
};

export function ProgressBar({
  value,
  max,
  variant = "default",
  showLabel = false,
}: ProgressBarProps) {
  const percentage = max > 0 ? Math.min((value / max) * 100, 100) : 0;

  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 rounded-full bg-bg-elevated overflow-hidden">
        <div
          className={[
            "h-full rounded-full transition-all duration-700 ease-out",
            "shadow-sm",
            barColors[variant],
            glowColors[variant],
          ].join(" ")}
          style={{ width: `${percentage}%` }}
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={max}
        />
      </div>
      {showLabel && (
        <span className="text-xs font-mono tabular-nums text-text-secondary min-w-[3rem] text-right">
          {percentage.toFixed(1)}%
        </span>
      )}
    </div>
  );
}
