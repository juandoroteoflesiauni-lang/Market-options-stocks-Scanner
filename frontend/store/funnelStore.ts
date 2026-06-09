"use client";

import { create } from "zustand";

import type {
  CandidateSnapshot,
  ExecutionSignal,
  FilteredAsset,
  FunnelOverview,
  OptionContract,
  SystemHealth,
} from "./types";

// ── Constants ─────────────────────────────────────────────────

const MAX_SIGNALS_DISPLAYED = 50;

// ── Store Interface ───────────────────────────────────────────

interface FunnelState {
  // Phase A — Scanner
  candidates: CandidateSnapshot[];

  // Phase B — Microstructure
  filteredAssets: FilteredAsset[];

  // Phase C — Derivatives
  selectedContracts: OptionContract[];

  // Phase D — Signals
  signals: ExecutionSignal[];

  // System
  funnelOverview: FunnelOverview | null;
  systemHealth: SystemHealth | null;
  isConnected: boolean;
  lastUpdated: string | null;

  // Actions — Phase Data
  setCandidates: (candidates: CandidateSnapshot[]) => void;
  setFilteredAssets: (assets: FilteredAsset[]) => void;
  setSelectedContracts: (contracts: OptionContract[]) => void;
  addSignal: (signal: ExecutionSignal) => void;
  clearSignals: () => void;

  // Actions — System
  setFunnelOverview: (overview: FunnelOverview) => void;
  setSystemHealth: (health: SystemHealth) => void;
  setConnected: (connected: boolean) => void;
}

// ── Store Implementation ──────────────────────────────────────

export const useFunnelStore = create<FunnelState>()((set) => ({
  // Initial state
  candidates: [],
  filteredAssets: [],
  selectedContracts: [],
  signals: [],
  funnelOverview: null,
  systemHealth: null,
  isConnected: false,
  lastUpdated: null,

  // Phase A
  setCandidates: (candidates) =>
    set({
      candidates,
      lastUpdated: new Date().toISOString(),
    }),

  // Phase B
  setFilteredAssets: (assets) =>
    set({
      filteredAssets: assets,
      lastUpdated: new Date().toISOString(),
    }),

  // Phase C
  setSelectedContracts: (contracts) =>
    set({
      selectedContracts: contracts,
      lastUpdated: new Date().toISOString(),
    }),

  // Phase D — Signals capped at MAX_SIGNALS_DISPLAYED
  addSignal: (signal) =>
    set((state) => ({
      signals: [signal, ...state.signals].slice(0, MAX_SIGNALS_DISPLAYED),
      lastUpdated: new Date().toISOString(),
    })),

  clearSignals: () => set({ signals: [] }),

  // System
  setFunnelOverview: (overview) => set({ funnelOverview: overview }),
  setSystemHealth: (health) => set({ systemHealth: health }),
  setConnected: (connected) => set({ isConnected: connected }),
}));
