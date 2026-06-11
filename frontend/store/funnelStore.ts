"use client";

import { create } from "zustand";

import type {
  SystemHealth,
  Ticker,
  Position,
  OptionContract,
  Universe,
} from "./types";

// ── Store Interface ───────────────────────────────────────────

export type TabId =
  | "SCANNER"
  | "BINGX"
  | "ALPACA"
  | "BINANCE"
  | "FUNDING"
  | "DERIVATIVES"
  | "TECHNICAL"
  | "PREDICTIVE";

interface FunnelState {
  // Navigation
  activeTab: TabId;

  // Tab 1: Scanner Data
  activeUniverseId: string | null;
  universes: Universe[];
  scannerResults: Ticker[];

  // Bots Data (BingX, Alpaca, Binance)
  positions: Position[];

  // Tab 6: Options Data
  optionsChain: OptionContract[];

  // System State
  systemHealth: SystemHealth | null;
  isConnected: boolean;
  lastUpdated: string | null;

  // Actions
  setActiveTab: (tabId: TabId) => void;
  setScannerResults: (results: Ticker[]) => void;
  fetchScannerCandidates: () => Promise<void>;
  setPositions: (positions: Position[]) => void;
  setOptionsChain: (chain: OptionContract[]) => void;
  setSystemHealth: (health: SystemHealth) => void;
  setConnected: (connected: boolean) => void;
}

// ── Store Implementation ──────────────────────────────────────

export const useFunnelStore = create<FunnelState>()((set) => ({
  // Initial state
  activeTab: "SCANNER",

  activeUniverseId: "tech-core",
  universes: [
    {
      id: "tech-core",
      name: "Tech Core",
      tickers: ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"],
    },
    { id: "high-iv", name: "High IV", tickers: ["TSLA", "MSTR", "COIN"] },
  ],
  scannerResults: [],

  positions: [],
  optionsChain: [],

  systemHealth: null,
  isConnected: false,
  lastUpdated: null,

  // Actions
  setActiveTab: (tabId) => set({ activeTab: tabId }),

  setScannerResults: (results) =>
    set({
      scannerResults: results,
      lastUpdated: new Date().toISOString(),
    }),

  fetchScannerCandidates: async () => {
    try {
      // Inline import to avoid circular dependencies if any, or just standard fetch
      const { fetchScannerCandidates: fetchApi } = await import("@/lib/api");
      const candidates = await fetchApi();
      set({
        scannerResults: candidates,
        lastUpdated: new Date().toISOString(),
      });
    } catch (error) {
      console.error("Failed to fetch scanner candidates:", error);
    }
  },

  setPositions: (positions) =>
    set({
      positions: positions,
      lastUpdated: new Date().toISOString(),
    }),

  setOptionsChain: (chain) =>
    set({
      optionsChain: chain,
      lastUpdated: new Date().toISOString(),
    }),

  setSystemHealth: (health) => set({ systemHealth: health }),
  setConnected: (connected) => set({ isConnected: connected }),
}));
