"use client"

import { motion } from "motion/react"
import { TABS, useTerminalStore } from "@/store/terminalStore"
import { cn } from "@/lib/terminal/format"

export function TerminalTabBar() {
  const activeTab = useTerminalStore((s) => s.activeTab)
  const setActiveTab = useTerminalStore((s) => s.setActiveTab)

  return (
    <nav
      aria-label="Terminal sections"
      className="sticky top-0 z-40 flex items-stretch gap-0.5 overflow-x-auto border-b border-border-subtle bg-bg-base/90 px-2 backdrop-blur-xl"
    >
      {TABS.map((tab) => {
        const active = tab.id === activeTab
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative flex shrink-0 items-center gap-2 px-4 py-3 font-mono text-[11px] uppercase tracking-widest transition-colors",
              active ? "text-text-accent" : "text-text-muted hover:text-text-secondary",
            )}
          >
            <span className={cn("text-[9px]", active ? "text-text-accent/70" : "text-text-muted/60")}>
              {tab.num}
            </span>
            {tab.label}
            {active && (
              <motion.span
                layoutId="tab-underline"
                className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-text-accent"
                transition={{ type: "spring", stiffness: 400, damping: 32 }}
              />
            )}
          </button>
        )
      })}
    </nav>
  )
}
