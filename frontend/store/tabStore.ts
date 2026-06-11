"use client";
import {
  createContext,
  useContext,
  useState,
  type ReactNode,
  createElement,
} from "react";
import type { TabId } from "@/types";

interface TabStore {
  activeTab: TabId;
  prevTab: TabId | null;
  direction: 1 | -1;
  setTab: (id: TabId) => void;
}

const TabContext = createContext<TabStore | null>(null);

const TAB_ORDER: TabId[] = [
  "scanner",
  "bingx",
  "alpaca",
  "binance",
  "funding",
  "derivatives",
  "technical",
  "predictive",
  "consumption",
  "audit",
];

export function TabProvider({ children }: { children: ReactNode }) {
  const [activeTab, setActiveTab] = useState<TabId>("scanner");
  const [prevTab, setPrevTab] = useState<TabId | null>(null);
  const [direction, setDirection] = useState<1 | -1>(1);

  function setTab(id: TabId) {
    const curr = TAB_ORDER.indexOf(activeTab);
    const next = TAB_ORDER.indexOf(id);
    setDirection(next >= curr ? 1 : -1);
    setPrevTab(activeTab);
    setActiveTab(id);
  }

  return createElement(
    TabContext.Provider,
    { value: { activeTab, prevTab, direction, setTab } },
    children,
  );
}

export function useTabStore(): TabStore {
  const ctx = useContext(TabContext);
  if (!ctx) throw new Error("useTabStore must be used within TabProvider");
  return ctx;
}
