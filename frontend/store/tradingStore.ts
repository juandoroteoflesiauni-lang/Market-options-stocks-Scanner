"use client";
import { create } from "zustand";
import type { StrategyWeights } from "@/types";

const DEFAULT_WEIGHTS: StrategyWeights = {
  regimeAdaptationEnabled: true,
  phaseA: {
    phaseWeight: 0.1,
    validationStrictness: 0.85,
    minPrice: 0.5,
    minVolume: 10_000,
    maxSpreadPct: 0.2,
  },
  phaseB: {
    phaseWeight: 0.25,
    ofiWeight: 0.45,
    smcWeight: 0.35,
    vpinWeight: 0.2,
    ofiSensitivity: 1.0,
    smcLookbackPeriods: 20,
    vpinBuckets: 50,
  },
  phaseC: {
    phaseWeight: 0.45,
    engineWeights: {
      gexScore: 0.2,
      gammaFlip: 0.12,
      dexExposure: 0.15,
      flowSignal: 0.12,
      zeroDay: 0.1,
      shadowDelta: 0.1,
      deltaFlow: 0.08,
      phaseBMomentum: 0.13,
    },
    contractScoreWeights: {
      basicMetrics: 0.4,
      engineAverage: 0.6,
      liquidity: 0.375,
      delta: 0.25,
      iv: 0.2,
      dte: 0.175,
    },
    contractFilters: {
      minVolume: 100,
      minOpenInterest: 500,
      maxSpreadPct: 0.15,
      minDte: 14,
      maxDte: 60,
      deltaTargetCall: 0.35,
      deltaTargetPut: -0.35,
      minCompositeScore: 40.0,
      ivMin: 0.1,
      ivMax: 0.4,
      optimalDte: 35,
    },
    topNTickers: 5,
    topNContracts: 5,
  },
  phaseD: {
    phaseWeight: 0.2,
    momentumWeight: 0.35,
    volatilityWeight: 0.25,
    volumeSpikeWeight: 0.2,
    vwapWeight: 0.1,
    phaseCConfluenceWeight: 0.1,
    entryMomentumThreshold: 0.003,
    exitMomentumThreshold: -0.002,
    volumeSpikeMultiplier: 2.5,
    minConfidence: 0.6,
    cooldownSeconds: 30,
    minTicksForSignal: 10,
    stopLossPct: 0.02,
    takeProfitPct: 0.04,
    momentumWindow: 20,
    volatilityWindow: 30,
  },
};

interface TradingStore {
  universe: import("@/types").Ticker[];
  isConnected: boolean;
  strategyWeights: StrategyWeights;
  setConnected: (status: boolean) => void;
  updateTicker: (
    updated: Partial<import("@/types").Ticker> & { symbol: string },
  ) => void;
  setStrategyWeights: (w: StrategyWeights) => void;
  updateWeight: (path: string, value: number) => void;
  resetWeights: () => void;
}

export const useTradingStore = create<TradingStore>((set) => ({
  universe: [],
  isConnected: false,
  strategyWeights: DEFAULT_WEIGHTS,
  setConnected: (v) => set({ isConnected: v }),
  updateTicker: (updated) =>
    set((state) => ({
      universe: state.universe.map((t) => {
        if (t.symbol === updated.symbol) {
          return { ...t, ...updated };
        }
        return t;
      }),
    })),
  setStrategyWeights: (w) => set({ strategyWeights: w }),
  updateWeight: (path, value) =>
    set((state) => {
      const w = { ...state.strategyWeights };
      setNested(w, path, value);
      return { strategyWeights: w };
    }),
  resetWeights: () => set({ strategyWeights: DEFAULT_WEIGHTS }),
}));

function setNested(
  obj: Record<string, unknown>,
  path: string,
  value: number,
): void {
  const parts = path.split(".");
  let cur: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    const key =
      Object.keys(cur).find(
        (k) =>
          k.toLowerCase() ===
          p
            .toLowerCase()
            .replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()),
      ) || p;
    if (!(key in cur)) return;
    cur = cur[key] as Record<string, unknown>;
  }
  const lastKey =
    Object.keys(cur).find(
      (k) =>
        k.toLowerCase() ===
        parts[parts.length - 1]
          .toLowerCase()
          .replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()),
    ) || parts[parts.length - 1];
  if (lastKey in cur) {
    cur[lastKey] = value;
  }
}
