/**
 * scannerService.test.ts — Unit tests for the Market Scanner service layer.
 *
 * Tests:
 *   - buildScanRequest: default filling, partial override
 *   - performScan: successful parse, error propagation, abort handling
 *   - fetchUniverses: successful parse, error propagation
 *   - fetchLivePrices: successful parse, abort handling
 *   - rowToDisplay: price string formatting, phase derivation, N/A handling
 *   - scanResponseToDisplay: batch transformation
 *   - displayToTicker: adapter correctness, edge cases
 *   - pingScanner: true/false on success/failure
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  buildScanRequest,
  performScan,
  fetchUniverses,
  fetchLivePrices,
  fetchContext,
  rowToDisplay,
  scanResponseToDisplay,
  displayToTicker,
  displayListToTickers,
  pingScanner,
} from "@/services/scannerService";
import {
  ApiError,
  NetworkError,
  TimeoutError,
  AuthError,
} from "@/lib/api-client";
import type {
  MarketScannerRow,
  MarketScannerResponse,
} from "@/types/marketScanner";

// ── Mock api-client ─────────────────────────────────────────────────────────

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual("@/lib/api-client");
  return {
    ...actual,
    fetchJson: vi.fn(),
  };
});

import { fetchJson } from "@/lib/api-client";

const mockFetchJson = vi.mocked(fetchJson);

// ── Fixtures ────────────────────────────────────────────────────────────────

function makeRow(overrides: Partial<MarketScannerRow> = {}): MarketScannerRow {
  return {
    symbol: "AAPL",
    price: 185.5,
    change_pct: 2.34,
    relative_volume: 1.2,
    scanner_score: 72.5,
    intraday_score: 65.0,
    swing_score: 78.0,
    setup_grade: "A",
    direction: "bullish",
    regime_label: "bull_quiet",
    signals: {
      "5m": { signal: "buy", strength: 0.8 },
      "1h": { signal: "buy", strength: 0.6 },
    },
    reasons: ["Strong momentum", "Volume spike"],
    warnings: [],
    vetoes: [],
    funding_suitability: "high",
    conviction_score: 0.85,
    capacity_signals: { capacity_score: 68.0 },
    universe_rank: 1,
    sparkline: [180, 181, 182, 183, 184, 185],
    deep_metrics: null,
    ...overrides,
  };
}

function makeScanResponse(rowCount = 1): MarketScannerResponse {
  return {
    rows: Array.from({ length: rowCount }, (_, i) =>
      makeRow({ symbol: `SYM${i}`, universe_rank: i + 1 }),
    ),
    meta: {
      universe: "wall_street",
      total_candidates: rowCount,
      scan_duration_ms: 150,
      regime: "bull_quiet",
      timestamp: "2026-01-01T00:00:00Z",
    },
  };
}

// ── Tests ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

// ── buildScanRequest ────────────────────────────────────────────────────────

describe("buildScanRequest", () => {
  it("fills all defaults when given empty params", () => {
    const req = buildScanRequest({});
    expect(req.universe).toBe("wall_street");
    expect(req.max_rows).toBe(50);
    expect(req.direction).toBe("both");
    expect(req.sort).toBe("scanner_score");
    expect(req.include_deep_metrics).toBe(false);
    expect(req.filters.min_price).toBe(1.0);
    expect(req.filters.min_volume).toBe(250_000);
    expect(req.timeframes).toEqual(["5m", "15m", "1h", "1D"]);
  });

  it("overrides defaults with provided params", () => {
    const req = buildScanRequest({
      universe: "custom",
      maxRows: 10,
      minScore: 50,
      direction: "long",
      includeDeepMetrics: true,
    });
    expect(req.universe).toBe("custom");
    expect(req.max_rows).toBe(10);
    expect(req.filters.min_score).toBe(50);
    expect(req.direction).toBe("long");
    expect(req.include_deep_metrics).toBe(true);
  });

  it("sets adaptive_weighting in customization", () => {
    const req = buildScanRequest({ adaptiveWeighting: true });
    expect(req.customization.adaptive_weighting).toBe(true);
  });
});

// ── performScan ─────────────────────────────────────────────────────────────

describe("performScan", () => {
  it("returns parsed MarketScannerResponse on success", async () => {
    const response = makeScanResponse(3);
    mockFetchJson.mockResolvedValueOnce(response);

    const result = await performScan(buildScanRequest({}));
    expect(result).toEqual(response);
    expect(mockFetchJson).toHaveBeenCalledWith(
      "/api/v1/market-scanner/scan",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(buildScanRequest({})),
      }),
    );
  });

  it("propagates ApiError on HTTP 500", async () => {
    mockFetchJson.mockRejectedValueOnce(
      new ApiError("Server error", 500, "/scan", true),
    );

    await expect(performScan(buildScanRequest({}))).rejects.toThrow(ApiError);
  });

  it("propagates AuthError on HTTP 401", async () => {
    mockFetchJson.mockRejectedValueOnce(
      new AuthError("Authentication required", "/scan"),
    );

    await expect(performScan(buildScanRequest({}))).rejects.toThrow(AuthError);
  });

  it("propagates TimeoutError", async () => {
    mockFetchJson.mockRejectedValueOnce(new TimeoutError("Request timeout"));

    await expect(performScan(buildScanRequest({}))).rejects.toThrow(
      TimeoutError,
    );
  });

  it("propagates NetworkError", async () => {
    mockFetchJson.mockRejectedValueOnce(new NetworkError("Failed to fetch"));

    await expect(performScan(buildScanRequest({}))).rejects.toThrow(
      NetworkError,
    );
  });

  it("passes AbortSignal to fetchJson", async () => {
    mockFetchJson.mockResolvedValueOnce(makeScanResponse());
    const controller = new AbortController();

    await performScan(buildScanRequest({}), controller.signal);

    expect(mockFetchJson).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});

// ── fetchUniverses ──────────────────────────────────────────────────────────

describe("fetchUniverses", () => {
  it("returns universe record on success", async () => {
    const data = {
      wall_street: { name: "Wall Street", symbols: ["AAPL", "MSFT"], count: 2 },
    };
    mockFetchJson.mockResolvedValueOnce(data);

    const result = await fetchUniverses();
    expect(result).toEqual(data);
    expect(mockFetchJson).toHaveBeenCalledWith(
      "/api/v1/market-scanner/universes",
      expect.any(Object),
    );
  });

  it("propagates errors", async () => {
    mockFetchJson.mockRejectedValueOnce(
      new ApiError("Not found", 404, "/universes"),
    );

    await expect(fetchUniverses()).rejects.toThrow(ApiError);
  });
});

// ── fetchLivePrices ─────────────────────────────────────────────────────────

describe("fetchLivePrices", () => {
  it("sends symbols array in request body", async () => {
    const response = {
      prices: {
        AAPL: {
          price: 190.0,
          change_pct: 1.5,
          source: "fmp",
          timestamp_ms: 1000,
        },
      },
    };
    mockFetchJson.mockResolvedValueOnce(response);

    const result = await fetchLivePrices(["AAPL", "MSFT"]);
    expect(result).toEqual(response);

    const callBody = JSON.parse(mockFetchJson.mock.calls[0][1]?.body as string);
    expect(callBody.symbols).toEqual(["AAPL", "MSFT"]);
  });
});

// ── fetchContext ────────────────────────────────────────────────────────────

describe("fetchContext", () => {
  it("sends universe and symbols in request body", async () => {
    const response = { summary: "Market is bullish", sentiment: "positive" };
    mockFetchJson.mockResolvedValueOnce(response);

    const result = await fetchContext("wall_street", ["AAPL", "MSFT"]);
    expect(result).toEqual(response);

    const callBody = JSON.parse(mockFetchJson.mock.calls[0][1]?.body as string);
    expect(callBody.universe).toBe("wall_street");
    expect(callBody.symbols).toEqual(["AAPL", "MSFT"]);
  });
});

// ── rowToDisplay ────────────────────────────────────────────────────────────

describe("rowToDisplay", () => {
  it("converts prices to strings (PD-2 compliance)", () => {
    const display = rowToDisplay(makeRow({ price: 185.5, change_pct: 2.34 }));
    expect(typeof display.price).toBe("string");
    expect(typeof display.change_pct).toBe("string");
    expect(display.price).toBe("185.50");
    expect(display.change_pct).toBe("2.34");
  });

  it("formats scores as strings", () => {
    const display = rowToDisplay(makeRow({ scanner_score: 72.5 }));
    expect(typeof display.scanner_score).toBe("string");
    expect(display.scanner_score).toBe("72.50");
  });

  it("derives phase A from 5m signals", () => {
    const display = rowToDisplay(
      makeRow({ signals: { "5m": { signal: "buy", strength: 0.8 } } }),
    );
    expect(display.phase).toBe("A");
  });

  it("derives phase A from 15m signals", () => {
    const display = rowToDisplay(
      makeRow({ signals: { "15m": { signal: "buy", strength: 0.8 } } }),
    );
    expect(display.phase).toBe("A");
  });

  it("derives phase B from 1h signals", () => {
    const display = rowToDisplay(
      makeRow({ signals: { "1h": { signal: "buy", strength: 0.6 } } }),
    );
    expect(display.phase).toBe("B");
  });

  it("derives phase C from 1D signals", () => {
    const display = rowToDisplay(
      makeRow({ signals: { "1D": { signal: "buy", strength: 0.5 } } }),
    );
    expect(display.phase).toBe("C");
  });

  it("returns N/A for null values", () => {
    const display = rowToDisplay(
      makeRow({ conviction_score: null, capacity_signals: undefined }),
    );
    expect(display.conviction_score).toBeNull();
    expect(display.capacity_score).toBeNull();
  });

  it("formats conviction_score as string when present", () => {
    const display = rowToDisplay(makeRow({ conviction_score: 0.85 }));
    expect(display.conviction_score).toBe("0.85");
  });
});

// ── scanResponseToDisplay ───────────────────────────────────────────────────

describe("scanResponseToDisplay", () => {
  it("transforms all rows in the response", () => {
    const response = makeScanResponse(5);
    const displayList = scanResponseToDisplay(response);
    expect(displayList).toHaveLength(5);
    displayList.forEach((d) => {
      expect(typeof d.price).toBe("string");
      expect(typeof d.scanner_score).toBe("string");
      expect(d.symbol).toMatch(/^SYM\d+$/);
    });
  });

  it("returns empty array for empty response", () => {
    const displayList = scanResponseToDisplay({ rows: [], meta: {} });
    expect(displayList).toEqual([]);
  });
});

// ── displayToTicker ─────────────────────────────────────────────────────────

describe("displayToTicker", () => {
  it("converts string prices back to numbers for legacy components", () => {
    const display = {
      symbol: "AAPL",
      price: "185.50",
      change_pct: "2.34",
      phase: "A" as const,
      scanner_score: "72.50",
      setup_grade: "A",
      direction: "bullish" as const,
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
    };

    const ticker = displayToTicker(display);
    expect(ticker.symbol).toBe("AAPL");
    expect(ticker.price).toBe(185.5);
    expect(ticker.priceChangePct).toBe(2.34);
    expect(ticker.phase).toBe("A");
    expect(typeof ticker.price).toBe("number");
  });

  it("handles zero/invalid prices gracefully", () => {
    const display = {
      symbol: "TEST",
      price: "N/A",
      change_pct: "N/A",
      phase: "A" as const,
      scanner_score: "0",
      setup_grade: "C",
      direction: "neutral" as const,
      intraday_score: "0",
      swing_score: "0",
      regime_label: null,
      sparkline: [],
      reasons: [],
      warnings: [],
      vetoes: [],
      funding_suitability: "low",
      conviction_score: null,
      capacity_score: null,
      universe_rank: null,
    };

    const ticker = displayToTicker(display);
    expect(ticker.price).toBe(0);
    expect(ticker.priceChangePct).toBe(0);
  });
});

// ── displayListToTickers ────────────────────────────────────────────────────

describe("displayListToTickers", () => {
  it("transforms an array of ScannerTickerDisplay to Ticker[]", () => {
    const displays = scanResponseToDisplay(makeScanResponse(3));
    const tickers = displayListToTickers(displays);
    expect(tickers).toHaveLength(3);
    tickers.forEach((t) => {
      expect(typeof t.price).toBe("number");
      expect(typeof t.priceChangePct).toBe("number");
    });
  });
});

// ── pingScanner ─────────────────────────────────────────────────────────────

describe("pingScanner", () => {
  it("returns true when backend responds", async () => {
    mockFetchJson.mockResolvedValueOnce({ ping: "pong" });
    const result = await pingScanner();
    expect(result).toBe(true);
  });

  it("returns false on any error", async () => {
    mockFetchJson.mockRejectedValueOnce(new NetworkError("Connection refused"));
    const result = await pingScanner();
    expect(result).toBe(false);
  });

  it("returns false on timeout", async () => {
    mockFetchJson.mockRejectedValueOnce(new TimeoutError("Timeout"));
    const result = await pingScanner();
    expect(result).toBe(false);
  });
});
