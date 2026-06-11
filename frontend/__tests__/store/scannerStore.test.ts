/**
 * scannerStore.test.ts — Unit tests for the Zustand scanner store.
 *
 * Tests:
 *   - Initial state shape and defaults
 *   - setTickers / setRawResponse / setUniverses / setLivePrices / setContext
 *   - selectUniverse (updates both selectedUniverse and params.universe)
 *   - updateParams (partial merge)
 *   - resetParams (restores defaults)
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useScannerStore } from "@/store/scannerStore";
import type { ScannerTickerDisplay } from "@/types/marketScanner";

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeTicker(
  overrides: Partial<ScannerTickerDisplay> = {},
): ScannerTickerDisplay {
  return {
    symbol: "AAPL",
    price: "185.50",
    change_pct: "2.34",
    phase: "A",
    scanner_score: "72.50",
    setup_grade: "A",
    direction: "bullish",
    intraday_score: "65.00",
    swing_score: "78.00",
    regime_label: "bull_quiet",
    sparkline: [180, 181, 182],
    reasons: ["Strong momentum"],
    warnings: [],
    vetoes: [],
    funding_suitability: "high",
    conviction_score: "0.85",
    capacity_score: "68.00",
    universe_rank: 1,
    ...overrides,
  };
}

// ── Tests ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  // Reset store to defaults before each test
  useScannerStore.setState({
    tickers: [],
    rawResponse: null,
    universes: {},
    livePrices: {},
    context: null,
    selectedUniverse: "wall_street",
    params: {
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
    },
  });
});

describe("scannerStore — initial state", () => {
  it("has empty tickers by default", () => {
    expect(useScannerStore.getState().tickers).toEqual([]);
  });

  it("has wall_street as default universe", () => {
    expect(useScannerStore.getState().selectedUniverse).toBe("wall_street");
    expect(useScannerStore.getState().params.universe).toBe("wall_street");
  });

  it("has correct default params", () => {
    const params = useScannerStore.getState().params;
    expect(params.direction).toBe("both");
    expect(params.maxRows).toBe(50);
    expect(params.sort).toBe("scanner_score");
  });
});

describe("scannerStore — setTickers", () => {
  it("replaces tickers", () => {
    const tickers = [
      makeTicker({ symbol: "AAPL" }),
      makeTicker({ symbol: "MSFT" }),
    ];
    useScannerStore.getState().setTickers(tickers);
    expect(useScannerStore.getState().tickers).toHaveLength(2);
    expect(useScannerStore.getState().tickers[0].symbol).toBe("AAPL");
  });
});

describe("scannerStore — setUniverses", () => {
  it("replaces universes map", () => {
    const universes = {
      wall_street: { name: "Wall Street", symbols: ["AAPL"], count: 1 },
    };
    useScannerStore.getState().setUniverses(universes);
    expect(Object.keys(useScannerStore.getState().universes)).toEqual([
      "wall_street",
    ]);
  });
});

describe("scannerStore — setLivePrices", () => {
  it("replaces livePrices", () => {
    const prices = { AAPL: { price: 190.0, change_pct: 1.5 } };
    useScannerStore.getState().setLivePrices(prices);
    expect(useScannerStore.getState().livePrices.AAPL.price).toBe(190.0);
  });
});

describe("scannerStore — selectUniverse", () => {
  it("updates selectedUniverse and params.universe together", () => {
    useScannerStore.getState().selectUniverse("tech");
    const state = useScannerStore.getState();
    expect(state.selectedUniverse).toBe("tech");
    expect(state.params.universe).toBe("tech");
  });

  it("resets other params to defaults when switching universe", () => {
    useScannerStore.getState().updateParams({ maxRows: 10 });
    useScannerStore.getState().selectUniverse("crypto");
    const state = useScannerStore.getState();
    expect(state.params.maxRows).toBe(50); // back to default
    expect(state.params.direction).toBe("both"); // back to default
  });
});

describe("scannerStore — updateParams", () => {
  it("merges partial params", () => {
    useScannerStore.getState().updateParams({ maxRows: 10, direction: "long" });
    const params = useScannerStore.getState().params;
    expect(params.maxRows).toBe(10);
    expect(params.direction).toBe("long");
    expect(params.sort).toBe("scanner_score"); // unchanged
  });

  it("can update a single param without affecting others", () => {
    useScannerStore.getState().updateParams({ minScore: 50 });
    expect(useScannerStore.getState().params.minScore).toBe(50);
    expect(useScannerStore.getState().params.maxRows).toBe(50);
  });
});

describe("scannerStore — resetParams", () => {
  it("restores all params to defaults", () => {
    useScannerStore
      .getState()
      .updateParams({ maxRows: 10, direction: "short" });
    useScannerStore.getState().selectUniverse("crypto");
    useScannerStore.getState().resetParams();

    const state = useScannerStore.getState();
    expect(state.params.universe).toBe("wall_street");
    expect(state.params.direction).toBe("both");
    expect(state.params.maxRows).toBe(50);
    expect(state.selectedUniverse).toBe("wall_street");
  });
});

describe("scannerStore — setContext", () => {
  it("replaces context", () => {
    const ctx = { summary: "Bullish market", sentiment: "positive" };
    useScannerStore.getState().setContext(ctx as any);
    expect(useScannerStore.getState().context).toEqual(ctx);
  });

  it("can set context to null", () => {
    useScannerStore.getState().setContext({ summary: "test" } as any);
    useScannerStore.getState().setContext(null);
    expect(useScannerStore.getState().context).toBeNull();
  });
});
