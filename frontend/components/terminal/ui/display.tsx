"use client"

import { useState, type ReactNode } from "react"
import { motion, AnimatePresence } from "motion/react"
import { cn, fmtPct } from "@/lib/terminal/format"
import type { BotState } from "@/lib/terminal/types"

/* ── BotStatusBadge ────────────────────────────────────────── */
const botStateMeta: Record<BotState, { label: string; dot: string; text: string; bg: string }> = {
  RUNNING: { label: "RUNNING", dot: "bg-signal-bull", text: "text-signal-bull", bg: "bg-signal-bull/10 border-signal-bull/30" },
  PAUSED: { label: "PAUSED", dot: "bg-signal-warn", text: "text-signal-warn", bg: "bg-signal-warn/10 border-signal-warn/30" },
  ERROR: { label: "ERROR", dot: "bg-signal-bear", text: "text-signal-bear", bg: "bg-signal-bear/10 border-signal-bear/30" },
  IDLE: { label: "IDLE", dot: "bg-signal-neutral", text: "text-signal-neutral", bg: "bg-signal-neutral/10 border-signal-neutral/30" },
}

export function BotStatusBadge({ state }: { state: BotState }) {
  const m = botStateMeta[state]
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest", m.bg, m.text)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", m.dot, state === "RUNNING" && "pulse-dot")} />
      {m.label}
    </span>
  )
}

/* ── RiskBar ───────────────────────────────────────────────── */
export function RiskBar({
  value,
  max = 100,
  label,
  invert,
}: {
  value: number
  max?: number
  label?: string
  invert?: boolean
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100))
  // invert => higher value is bad (red)
  const ratio = invert ? pct / 100 : 1 - pct / 100
  const color = ratio > 0.66 ? "var(--color-bear)" : ratio > 0.33 ? "var(--color-warn)" : "var(--color-bull)"
  return (
    <div className="w-full">
      {label && (
        <div className="mb-1 flex items-center justify-between font-mono text-[10px] text-text-muted">
          <span>{label}</span>
          <span style={{ color }}>{pct.toFixed(0)}%</span>
        </div>
      )}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-elevated">
        <motion.div
          className="h-full rounded-full"
          style={{ backgroundColor: color }}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
        />
      </div>
    </div>
  )
}

/* ── WeightSlider ──────────────────────────────────────────── */
export function WeightSlider({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 truncate font-mono text-[11px] text-text-secondary">{label}</span>
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="terminal-range h-1 flex-1 cursor-pointer appearance-none rounded-full bg-bg-elevated"
        style={{
          background: `linear-gradient(to right, var(--color-accent) ${value}%, var(--color-bg-elevated) ${value}%)`,
        }}
      />
      <span className="w-10 text-right font-mono text-[11px] tabular-nums text-text-accent">{value}%</span>
    </div>
  )
}

/* ── ExpandableCard ────────────────────────────────────────── */
export function ExpandableCard({
  header,
  children,
  defaultOpen = false,
  className,
}: {
  header: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={cn("overflow-hidden rounded-xl border border-border-subtle bg-bg-panel/70", className)}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left transition-colors hover:bg-bg-hover"
        aria-expanded={open}
      >
        <div className="min-w-0 flex-1">{header}</div>
        <motion.span animate={{ rotate: open ? 180 : 0 }} className="shrink-0 text-text-muted" aria-hidden>
          ▾
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="border-t border-border-subtle px-4 py-3">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* ── DataTable ─────────────────────────────────────────────── */
export interface Column<T> {
  key: string
  header: string
  align?: "left" | "right" | "center"
  width?: string
  render: (row: T) => ReactNode
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  dense,
  emptyLabel = "NO DATA",
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T, i: number) => string
  onRowClick?: (row: T) => void
  dense?: boolean
  emptyLabel?: string
}) {
  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-border-subtle">
            {columns.map((c) => (
              <th
                key={c.key}
                style={{ width: c.width, textAlign: c.align ?? "left" }}
                className="whitespace-nowrap px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-widest text-text-muted"
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center font-mono text-[11px] text-text-muted">
                {emptyLabel}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={rowKey(row, i)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(
                  "border-b border-border-subtle/60 transition-colors",
                  onRowClick && "cursor-pointer hover:bg-bg-hover",
                )}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    style={{ textAlign: c.align ?? "left" }}
                    className={cn(
                      "whitespace-nowrap px-3 font-mono text-xs tabular-nums text-text-primary",
                      dense ? "py-1.5" : "py-2.5",
                    )}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
