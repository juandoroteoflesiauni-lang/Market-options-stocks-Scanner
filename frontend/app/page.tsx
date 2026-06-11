"use client";
import React from "react";

import { TabProvider, useTabStore } from "@/store/tabStore";
import { Shell } from "@/components/layout/Shell";
import { TabTransition } from "@/components/layout/TabTransition";
import { MarketScanner } from "@/components/tabs/MarketScanner";
import { BingXBot } from "@/components/tabs/BingXBot";
import { AlpacaBot } from "@/components/tabs/AlpacaBot";
import { BinanceBot } from "@/components/tabs/BinanceBot";
import { FundingChallenge } from "@/components/tabs/FundingChallenge";
import { Derivatives } from "@/components/tabs/Derivatives";
import { Technical } from "@/components/tabs/Technical";
import { Predictive } from "@/components/tabs/Predictive";
import { ApiConsumptionMonitor } from "@/components/tabs/ApiConsumptionMonitor";
import { AuditComplex } from "@/components/tabs/AuditComplex";
import type { TabId } from "@/types";

// ── Tab definitions ─────────────────────────────────────────────────────────
const TAB_CONTENT: Record<TabId, React.JSX.Element> = {
  scanner: <MarketScanner />,
  bingx: <BingXBot />,
  alpaca: <AlpacaBot />,
  binance: <BinanceBot />,
  funding: <FundingChallenge />,
  derivatives: <Derivatives />,
  technical: <Technical />,
  predictive: <Predictive />,
  consumption: <ApiConsumptionMonitor />,
  audit: <AuditComplex />,
};

import { useWebSocket } from "@/hooks/useWebSocket";

// ── Main routed view ─────────────────────────────────────────────────────────
function AppContent() {
  const { activeTab } = useTabStore();
  useWebSocket(); // Mount real-time data connection

  return (
    <Shell>
      <TabTransition tabKey={activeTab}>{TAB_CONTENT[activeTab]}</TabTransition>
    </Shell>
  );
}

export default function Home() {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => {
    const id = setTimeout(() => setMounted(true), 0);
    return () => clearTimeout(id);
  }, []);

  if (!mounted) return null;

  return (
    <TabProvider>
      <AppContent />
    </TabProvider>
  );
}
