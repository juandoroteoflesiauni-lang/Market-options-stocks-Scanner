"use client"

import { AnimatePresence, motion } from "motion/react"
import { useTerminalStore } from "@/store/terminalStore"
import { TerminalHeader } from "./TerminalHeader"
import { TerminalTabBar } from "./TerminalTabBar"
import { TerminalStatusBar } from "./TerminalStatusBar"
import { ScannerTab } from "@/components/terminal/tabs/ScannerTab"
import { BingxTab } from "@/components/terminal/tabs/BingxTab"
import { AlpacaTab } from "@/components/terminal/tabs/AlpacaTab"
import { BinanceTab } from "@/components/terminal/tabs/BinanceTab"
import { FundingTab } from "@/components/terminal/tabs/FundingTab"
import { DerivadosTab } from "@/components/terminal/tabs/DerivadosTab"
import { TecnicoTab } from "@/components/terminal/tabs/TecnicoTab"
import { PredictivoTab } from "@/components/terminal/tabs/PredictivoTab"

const TAB_CONTENT = {
  scanner: ScannerTab,
  bingx: BingxTab,
  alpaca: AlpacaTab,
  binance: BinanceTab,
  funding: FundingTab,
  derivados: DerivadosTab,
  tecnico: TecnicoTab,
  predictivo: PredictivoTab,
} as const

export function TerminalShell() {
  const activeTab = useTerminalStore((s) => s.activeTab)
  const ActiveComponent = TAB_CONTENT[activeTab]

  return (
    <div className="flex min-h-screen flex-col">
      <TerminalHeader />
      <TerminalTabBar />
      <main className="relative flex-1 overflow-x-hidden px-3 py-4 sm:px-4 lg:px-6">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <ActiveComponent />
          </motion.div>
        </AnimatePresence>
      </main>
      <TerminalStatusBar />
    </div>
  )
}
