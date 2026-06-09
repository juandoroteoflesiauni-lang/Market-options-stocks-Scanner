"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/terminal/format";

/* ── Panel ─────────────────────────────────────────────────── */
export function Panel({
  children,
  className,
  title,
  subtitle,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <section
      className={cn(
        "rounded-[14px] border border-border-subtle bg-bg-panel/80 backdrop-blur-md",
        className,
      )}
    >
      {(title || action) && (
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-2.5">
          <div>
            {title && (
              <h3 className="font-mono text-[11px] uppercase tracking-widest text-text-secondary">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-[10px] text-text-muted">{subtitle}</p>
            )}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/* ── Button ────────────────────────────────────────────────── */
type BtnVariant = "default" | "accent" | "warn" | "danger" | "ghost";

const btnVariants: Record<BtnVariant, string> = {
  default:
    "bg-bg-elevated border-border-muted text-text-primary hover:bg-bg-hover",
  accent:
    "bg-text-accent/15 border-border-accent text-text-accent hover:bg-text-accent/25",
  warn: "bg-signal-warn/15 border-signal-warn/40 text-signal-warn hover:bg-signal-warn/25",
  danger:
    "bg-signal-bear/15 border-signal-bear/40 text-signal-bear hover:bg-signal-bear/25",
  ghost: "bg-transparent border-transparent text-text-secondary hover:text-text-primary hover:bg-bg-hover",
};

export function Button({
  children,
  variant = "default",
  className,
  onClick,
  type = "button",
  title,
  "aria-label": ariaLabel,
}: {
  children: ReactNode;
  variant?: BtnVariant;
  className?: string;
  onClick?: () => void;
  type?: "button" | "submit";
  title?: string;
  "aria-label"?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5",
        "font-mono text-[11px] uppercase tracking-widest transition-all duration-100",
        "active:scale-[0.97]",
        btnVariants[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}

/* ── Chip ──────────────────────────────────────────────────── */
export function Chip({
  children,
  active,
  onClick,
  className,
}: {
  children: ReactNode;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest transition-all duration-100",
        active
          ? "border-border-accent bg-text-accent/15 text-text-accent"
          : "border-border-muted bg-bg-panel text-text-secondary hover:bg-bg-hover hover:text-text-primary",
        className,
      )}
    >
      {children}
    </button>
  );
}

/* ── Tag (signal-colored pill) ─────────────────────────────── */
export function Tag({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "bull" | "bear" | "warn" | "info" | "neutral";
  className?: string;
}) {
  const tones: Record<string, string> = {
    bull: "bg-signal-bull/15 text-signal-bull border-signal-bull/30",
    bear: "bg-signal-bear/15 text-signal-bear border-signal-bear/30",
    warn: "bg-signal-warn/15 text-signal-warn border-signal-warn/30",
    info: "bg-signal-info/15 text-signal-info border-signal-info/30",
    neutral: "bg-signal-neutral/10 text-signal-neutral border-signal-neutral/30",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ── Input ─────────────────────────────────────────────────── */
export function Input({
  value,
  onChange,
  placeholder,
  className,
  type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "no-spinner rounded-md border border-border-muted bg-bg-base/60 px-2.5 py-1.5",
        "font-mono text-xs text-text-primary placeholder:text-text-muted",
        "outline-none transition-colors focus:border-border-accent",
        className,
      )}
    />
  );
}
