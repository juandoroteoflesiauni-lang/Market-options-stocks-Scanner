import type { ReactNode } from "react";

type BadgeVariant = "default" | "buy" | "sell" | "neutral" | "warning";

interface BadgeProps {
  children: ReactNode;
  variant?: BadgeVariant;
}

const variantClasses: Record<BadgeVariant, string> = {
  default: "bg-bg-elevated text-text-secondary border-border-default",
  buy: "bg-signal-buy/15 text-signal-buy border-signal-buy/30",
  sell: "bg-signal-sell/15 text-signal-sell border-signal-sell/30",
  neutral: "bg-signal-neutral/15 text-signal-neutral border-signal-neutral/30",
  warning: "bg-signal-warning/15 text-signal-warning border-signal-warning/30",
};

export function Badge({ children, variant = "default" }: BadgeProps) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1",
        "rounded-full border px-2.5 py-0.5",
        "text-xs font-medium tracking-wide uppercase",
        "transition-colors duration-150",
        variantClasses[variant],
      ].join(" ")}
    >
      {children}
    </span>
  );
}
