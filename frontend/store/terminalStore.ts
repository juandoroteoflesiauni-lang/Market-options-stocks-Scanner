"use client";

import { create } from "zustand";

export type TabId =
  | "scanner"
  | "bingx"
  | "alpaca"
  | "binance"
  | "funding"
  | "derivados"
  | "tecnico"
  | "predictivo";

export interface TabDef {
  id: TabId;
  num: string;
  label: string;
}

export const TABS: TabDef[] = [
  { id: "scanner", num: "01", label: "SCANNER" },
  { id: "bingx", num: "02", label: "BINGX" },
  { id: "alpaca", num: "03", label: "ALPACA" },
  { id: "binance", num: "04", label: "BINANCE" },
  { id: "funding", num: "05", label: "FUNDING" },
  { id: "derivados", num: "06", label: "DERIVADOS" },
  { id: "tecnico", num: "07", label: "TÉCNICO" },
  { id: "predictivo", num: "08", label: "PREDICTIVO" },
];

interface TerminalState {
  activeTab: TabId;
  connected: boolean;
  setActiveTab: (tab: TabId) => void;
  setConnected: (c: boolean) => void;
}

export const useTerminalStore = create<TerminalState>()((set) => ({
  activeTab: "scanner",
  connected: true,
  setActiveTab: (activeTab) => set({ activeTab }),
  setConnected: (connected) => set({ connected }),
}));
