/**
 * useScanner.test.ts — Unit tests for the useScanner React hook.
 *
 * Tests:
 *   - Initial loading state
 *   - scan() calls performScan and updates store
 *   - setUniverse() updates store
 *   - updateParams() updates store and triggers scan
 *   - clearError() resets error state
 *   - retry() re-calls scan
 *   - refreshPrices() calls fetchLivePrices
 *   - loadContext() calls fetchContext
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useScanner } from "@/hooks/useScanner";
import { useScannerStore } from "@/store/scannerStore";
import type { ScannerTickerDisplay } from "@/types/marketScanner";

// ── Mocks ────────────────────────────────────────────────────────────────────

const mockPerformScan = vi.fn();
const mockFetchUniverses = vi.fn();
const mockFetchLivePrices = vi.fn();
const mockFetchContext = vi.fn();
const mockBuildScanRequest = vi.fn();
const mockScanResponseToDisplay = vi.fn();

vi.mock("@/services/scannerService", () => ({
  performScan: (...args: unknown[]) => mockPerformScan(...args),
  fetchUniverses: (...args: unknown[]) => mockFetchUniverses(...args),
  fetchLivePrices: (...args: unknown[]) => mockFetchLivePrices(...args),
  fetchContext: (...args: unknown[]) => mockFetchContext(...args),
  buildScanRequest: (...args: unknown[]) => mockBuildScanRequest(...args),
  scanResponseToDisplay: (...args: unknown[]) =>
    mockScanResponseToDisplay(...args),
}));

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

function makeScanResponse() {
  return {
    universe: "all",
    generated_at: new Date().toISOString(),
    rows: [{ symbol: "AAPL", price: 185.5, scanner_score: 72.5 }],
    meta: { total_candidates: 1, universe_size: 5000 },
  };
}

// ── Tests ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();

  // Default mock implementations
  mockBuildScanRequest.mockReturnValue({
    universe: "all",
    max_rows: 50,
    min_score: 40,
    min_price: 5,
    min_volume: 1_000_000,
    timeframes: ["5m", "15m", "1h", "1D"],
    direction: null,
    sort: "score",
    include_deep_metrics: false,
    adaptive_weighting: false,
  });
  mockScanResponseToDisplay.mockReturnValue([makeTicker()]);
  mockFetchUniverses.mockResolvedValue({});
  mockPerformScan.mockResolvedValue(makeScanResponse());
  mockFetchLivePrices.mockResolvedValue({ prices: {} });
  mockFetchContext.mockResolvedValue({});

  // Reset store
  useScannerStore.setState({
    tickers: [],
    rawResponse: null,
    universes: {},
    livePrices: {},
    context: null,
    selectedUniverse: "all",
    params: {
      universe: "all",
      maxRows: 50,
      minScore: 40,
      minPrice: 5,
      minVolume: 1_000_000,
      timeframes: ["5m", "15m", "1h", "1D"],
      direction: null,
      sort: "score",
      includeDeepMetrics: false,
      adaptiveWeighting: false,
    },
  });
});

describe("useScanner", () => {
  it("starts in loading state", () => {
    const { result } = renderHook(() => useScanner());

    expect(result.current.isLoading).toBe(true);
    expect(result.current.isScanning).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("calls scan on mount and updates tickers", async () => {
    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(mockPerformScan).toHaveBeenCalled();
    expect(result.current.tickers).toHaveLength(1);
    expect(result.current.tickers[0].symbol).toBe("AAPL");
  });

  it("calls fetchUniverses on mount", async () => {
    renderHook(() => useScanner());

    await waitFor(() => {
      expect(mockFetchUniverses).toHaveBeenCalled();
    });
  });

  it("scan() sets error on failure", async () => {
    mockPerformScan.mockRejectedValue(new Error("Network error"));

    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });

    expect(result.current.error?.message).toBe("Network error");
    expect(result.current.isScanning).toBe(false);
  });

  it("setUniverse() updates selectedUniverse in store", async () => {
    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    act(() => {
      result.current.setUniverse("crypto");
    });

    expect(result.current.selectedUniverse).toBe("crypto");
  });

  it("updateParams() updates store params", async () => {
    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    act(() => {
      result.current.updateParams({ minScore: 60 });
    });

    expect(result.current.requestParams.minScore).toBe(60);
  });

  it("clearError() resets error to null", async () => {
    mockPerformScan.mockRejectedValue(new Error("fail"));

    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });

    act(() => {
      result.current.clearError();
    });

    expect(result.current.error).toBeNull();
  });

  it("retry() calls scan again after error", async () => {
    mockPerformScan
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValueOnce(makeScanResponse());

    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });

    await act(async () => {
      result.current.retry();
    });

    await waitFor(() => {
      expect(result.current.error).toBeNull();
    });

    expect(mockPerformScan).toHaveBeenCalledTimes(2);
  });

  it("refreshPrices() calls fetchLivePrices", async () => {
    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    useScannerStore.setState({ tickers: [makeTicker()] });

    await act(async () => {
      await result.current.refreshPrices();
    });

    expect(mockFetchLivePrices).toHaveBeenCalled();
  });

  it("loadContext() calls fetchContext", async () => {
    const { result } = renderHook(() => useScanner());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    useScannerStore.setState({ tickers: [makeTicker()] });

    await act(async () => {
      await result.current.loadContext();
    });

    expect(mockFetchContext).toHaveBeenCalled();
  });
});
