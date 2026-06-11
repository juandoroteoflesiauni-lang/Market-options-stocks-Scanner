"use client";
import { create } from "zustand";
import type {
  MarketScannerResponse,
  MarketScannerUniverse,
  MarketScannerContextResponse,
  ScannerTickerDisplay,
  ScannerTimeframe,
} from "@/types/marketScanner";

// ── Scan Params (shared type, owned by the store) ───────────────────────────

export interface ScanParams {
  universe: string;
  direction: "long" | "short" | "both";
  maxRows: number;
  minScore: number;
  minPrice: number;
  minVolume: number;
  timeframes: ScannerTimeframe[];
  sort:
    | "scanner_score"
    | "conviction_score"
    | "regime_fit_score"
    | "capacity_score"
    | "symbol"
    | "change_pct"
    | "relative_volume"
    | "universe_percentile";
  includeDeepMetrics: boolean;
  adaptiveWeighting: boolean;
}

// ── Default Params ───────────────────────────────────────────────────────────

const DEFAULT_PARAMS: ScanParams = {
  universe: "wall_street",
  direction: "both",
  maxRows: 50,
  minScore: 0,
  minPrice: 1.0,
  minVolume: 250_000,
  timeframes: ["5m", "15m", "1h", "1D"],
  sort: "scanner_score",
  includeDeepMetrics: false,
  adaptiveWeighting: true,
};

// ── State Shape ──────────────────────────────────────────────────────────────

export interface ScannerDataState {
  /** Current scan results (display-ready). */
  tickers: ScannerTickerDisplay[];
  /** Raw backend response (for advanced UI like score_audit). */
  rawResponse: MarketScannerResponse | null;
  /** Available universes from the backend. */
  universes: Record<string, MarketScannerUniverse>;
  /** Live price overrides (symbol → price). */
  livePrices: Record<string, { price: number; change_pct: number | null }>;
  /** Market context / brief. */
  context: MarketScannerContextResponse | null;
  /** Currently selected universe key. */
  selectedUniverse: string;
  /** Current scan request parameters. */
  params: ScanParams;
}

export interface ScannerDataActions {
  /** Replace all scan results. */
  setTickers: (tickers: ScannerTickerDisplay[]) => void;
  /** Set the raw backend response. */
  setRawResponse: (response: MarketScannerResponse | null) => void;
  /** Replace the universes map. */
  setUniverses: (universes: Record<string, MarketScannerUniverse>) => void;
  /** Merge live price overrides. */
  setLivePrices: (
    prices: Record<string, { price: number; change_pct: number | null }>,
  ) => void;
  /** Update a single ticker's live price (from WebSocket push). */
  updateTickerPrice: (
    symbol: string,
    price: number,
    change_pct: number | null,
  ) => void;
  /** Set market context. */
  setContext: (context: MarketScannerContextResponse | null) => void;
  /** Change the selected universe (also updates params.universe). */
  selectUniverse: (universe: string) => void;
  /** Merge partial scan params. */
  updateParams: (partial: Partial<ScanParams>) => void;
  /** Reset to default params. */
  resetParams: () => void;
}

export type ScannerStore = ScannerDataState & ScannerDataActions;

// ── Store ────────────────────────────────────────────────────────────────────

export const useScannerStore = create<ScannerStore>((set) => ({
  // ── Data state ───────────────────────────────────────────────────────────
  tickers: [],
  rawResponse: null,
  universes: {},
  livePrices: {},
  context: null,
  selectedUniverse: DEFAULT_PARAMS.universe,
  params: { ...DEFAULT_PARAMS },

  // ── Actions ──────────────────────────────────────────────────────────────
  setTickers: (tickers) => set({ tickers }),
  setRawResponse: (rawResponse) => set({ rawResponse }),
  setUniverses: (universes) => set({ universes }),
  setLivePrices: (livePrices) => set({ livePrices }),
  updateTickerPrice: (symbol, price, change_pct) =>
    set((state) => ({
      livePrices: {
        ...state.livePrices,
        [symbol]: { price, change_pct },
      },
    })),
  setContext: (context) => set({ context }),

  selectUniverse: (universe) =>
    set({
      selectedUniverse: universe,
      params: { ...DEFAULT_PARAMS, universe },
    }),

  updateParams: (partial) =>
    set((state) => ({
      params: { ...state.params, ...partial },
    })),

  resetParams: () =>
    set({
      params: { ...DEFAULT_PARAMS },
      selectedUniverse: DEFAULT_PARAMS.universe,
    }),
}));
