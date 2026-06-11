import * as React from "react";
import { cn } from "@/lib/utils";

export type TerminalTone = "bull" | "bear" | "info" | "warn" | "neutral";

// StatusBadge: Renders a compact badge representing status
export function StatusBadge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: TerminalTone;
  children: React.ReactNode;
  className?: string;
}) {
  const toneClasses = {
    bull: "border-emerald-500/20 bg-emerald-500/10 text-emerald-400",
    bear: "border-rose-500/20 bg-rose-500/10 text-rose-400",
    info: "border-blue-500/20 bg-blue-500/10 text-blue-400",
    warn: "border-amber-500/20 bg-amber-500/10 text-amber-400",
    neutral: "border-zinc-700/30 bg-zinc-800/30 text-zinc-400",
  };

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 border px-2 py-0.5 rounded-full font-mono text-[10px] font-medium uppercase tracking-wider whitespace-nowrap",
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </div>
  );
}

// SourceBadge: A simpler badge, similar to status badge or inline chip
export function SourceBadge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: TerminalTone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <StatusBadge tone={tone} className={className}>
      {children}
    </StatusBadge>
  );
}

// MetricCell: A dashboard metric cell for command decks and readiness panels
export function MetricCell({
  label,
  value,
  detail,
  tone = "neutral",
  className,
}: {
  label: string;
  value: React.ReactNode;
  detail?: string;
  tone?: TerminalTone;
  className?: string;
}) {
  const toneClasses = {
    bull: "text-emerald-400",
    bear: "text-rose-400",
    info: "text-blue-400",
    warn: "text-amber-400",
    neutral: "text-zinc-100",
  };

  return (
    <div
      className={cn(
        "bg-[#18181b]/20 border border-white/5 p-3 rounded-xl backdrop-blur-sm flex flex-col justify-between",
        className,
      )}
    >
      <div>
        <div className="text-[9px] text-zinc-500 font-mono tracking-wider mb-1 uppercase">
          {label}
        </div>
        <div
          className={cn(
            "text-lg font-mono font-bold tracking-tight",
            toneClasses[tone],
          )}
        >
          {value}
        </div>
      </div>
      {detail && (
        <div className="text-[8px] font-mono text-zinc-600 mt-1.5">
          {detail}
        </div>
      )}
    </div>
  );
}

// TerminalPanel: A pane with a header and a body, styled like a trading window
export function TerminalPanel({
  title,
  eyebrow,
  source,
  actions,
  className,
  children,
}: {
  title: string;
  eyebrow?: React.ReactNode;
  source?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-2xl bg-[#121215]/30 border border-white/5 flex flex-col backdrop-blur-[22px] shadow-[0_24px_50px_rgba(0,0,0,0.5)] overflow-hidden transition-all hover:border-white/10",
        className,
      )}
    >
      <div className="p-4 border-b border-white/5 flex items-center justify-between">
        <div className="flex flex-col">
          {eyebrow && typeof eyebrow === "string" ? (
            <span className="text-[9px] text-zinc-500 font-mono uppercase tracking-widest mb-0.5">
              {eyebrow}
            </span>
          ) : (
            eyebrow
          )}
          <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
            {title}
          </h2>
        </div>
        <div className="flex items-center gap-3">
          {source && <div className="flex items-center gap-1.5">{source}</div>}
          {actions && (
            <div className="flex items-center gap-1.5">{actions}</div>
          )}
        </div>
      </div>
      <div className="flex-1 p-4 overflow-y-auto custom-scrollbar">
        {children}
      </div>
    </div>
  );
}

// EmptyState: Renders a message when there's no data
export function EmptyState({
  title,
  description,
  className,
}: {
  title: string;
  description?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-12 px-4 text-center h-full border border-dashed border-white/5 rounded-xl bg-white/[0.01]",
        className,
      )}
    >
      <h3 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider font-mono">
        {title}
      </h3>
      {description && (
        <p className="text-xs text-zinc-500 font-mono mt-1 max-w-xs">
          {description}
        </p>
      )}
    </div>
  );
}
