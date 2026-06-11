/**
 * Market Scanner service — Backend integration layer.
 *
 * All scanner API calls go through this service. It uses the existing
 * fetchJson client from lib/api-client.ts for HTTP transport.
 *
 * Design rules:
 *   - No business logic here (only data fetching + light transformation).
 *   - No floating point for prices (PD-2): pass strings to the UI.
 *   - All errors propagate as Error objects (no silent swallowing).
 *   - AbortController support for request cancellation.
 *
 * @module services/scannerService
 */

import { fetchJson } from "@/lib/api-client";
import {
  SCANNER_API_TIMEOUT_MS,
  SCANNER_LIVE_PRICE_TIMEOUT_MS,
  SCANNER_CONTEXT_TIMEOUT_MS,
} from "@/lib/constants";
import type {
  MarketScannerRequest,
  MarketScannerResponse,
  MarketScannerUniverse,
  MarketScannerPreset,
  MarketScannerLivePricesRequest,
  MarketScannerLivePricesResponse,
  MarketScannerContextResponse,
  MarketScannerIndicatorCatalogResponse,
  ScannerTickerDisplay,
  ScannerTimeframe,
  MarketScannerRow,
} from "@/types/marketScanner";

// ── API Endpoints ───────────────────────────────────────────────────────────

const ENDPOINTS = {
  SCAN: "/api/v1/market-scanner/scan",
  UNIVERSES: "/api/v1/market-scanner/universes",
  PRESETS: "/api/v1/market-scanner/presets",
  INDICATORS: "/api/v1/market-scanner/indicators",
  PRICES: "/api/v1/market-scanner/prices",
  CONTEXT: "/api/v1/market-scanner/context",
  PING: "/api/v1/market-scanner/ping",
} as const;

// ── Request Builder ─────────────────────────────────────────────────────────

/**
 * Builds a MarketScannerRequest from partial UI state.
 * Fills defaults for missing fields.
 */
export function buildScanRequest(params: {
  universe?: string;
  symbols?: string[];
  timeframes?: ScannerTimeframe[];
  direction?: MarketScannerRequest["direction"];
  maxRows?: number;
  minScore?: number;
  minPrice?: number;
  minVolume?: number;
  includeDeepMetrics?: boolean;
  sort?: MarketScannerRequest["sort"];
  adaptiveWeighting?: boolean;
}): MarketScannerRequest {
  return {
    universe: params.universe ?? "wall_street",
    symbols: params.symbols ?? [],
    timeframes: params.timeframes ?? ["5m", "15m", "1h", "1D"],
    filters: {
      min_price: params.minPrice ?? 1.0,
      min_volume: params.minVolume ?? 250_000,
      min_relative_volume: 0.5,
      min_score: params.minScore ?? 0,
      allow_reversal: true,
      include_vetoed: false,
    },
    sort: params.sort ?? "scanner_score",
    direction: params.direction ?? "both",
    max_rows: params.maxRows ?? 50,
    include_deep_metrics: params.includeDeepMetrics ?? false,
    customization: {
      enabled_indicators: null,
      enabled_modules: null,
      weight_matrix: {},
      module_synthesis_limit: 10,
      primary_timeframe: null,
      adaptive_weighting: params.adaptiveWeighting ?? false,
      scoring_schema_version: null,
    },
    webhook_url: null,
    include_funding_gate: true,
  };
}

// ── Core API Functions ──────────────────────────────────────────────────────

/**
 * Health check — verifies the scanner backend is reachable.
 */
export async function pingScanner(signal?: AbortSignal): Promise<boolean> {
  try {
    await fetchJson<Record<string, string>>(ENDPOINTS.PING, {
      timeoutMs: SCANNER_LIVE_PRICE_TIMEOUT_MS,
      signal,
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Fetch available symbol universes from the backend.
 */
export async function fetchUniverses(
  signal?: AbortSignal,
): Promise<Record<string, MarketScannerUniverse>> {
  return fetchJson<Record<string, MarketScannerUniverse>>(ENDPOINTS.UNIVERSES, {
    timeoutMs: SCANNER_API_TIMEOUT_MS,
    signal,
  });
}

/**
 * Fetch built-in scanner presets.
 */
export async function fetchPresets(
  signal?: AbortSignal,
): Promise<MarketScannerPreset[]> {
  return fetchJson<MarketScannerPreset[]>(ENDPOINTS.PRESETS, {
    timeoutMs: SCANNER_API_TIMEOUT_MS,
    signal,
  });
}

/**
 * Fetch the versioned scanner indicator catalog.
 */
export async function fetchIndicatorCatalog(
  signal?: AbortSignal,
): Promise<MarketScannerIndicatorCatalogResponse> {
  return fetchJson<MarketScannerIndicatorCatalogResponse>(
    ENDPOINTS.INDICATORS,
    { timeoutMs: SCANNER_API_TIMEOUT_MS, signal },
  );
}

/**
 * Execute a full scanner scan and return ranked results.
 *
 * This is the primary data-fetching function for the scanner tab.
 * Returns raw backend response — no transformation applied.
 */
export async function performScan(
  request: MarketScannerRequest,
  signal?: AbortSignal,
): Promise<MarketScannerResponse> {
  return fetchJson<MarketScannerResponse>(ENDPOINTS.SCAN, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    timeoutMs: SCANNER_API_TIMEOUT_MS,
    signal,
  });
}

/**
 * Fetch lightweight live prices for a batch of symbols.
 * Used for price refresh without re-running the full scan.
 */
export async function fetchLivePrices(
  symbols: string[],
  signal?: AbortSignal,
): Promise<MarketScannerLivePricesResponse> {
  const request: MarketScannerLivePricesRequest = { symbols };
  return fetchJson<MarketScannerLivePricesResponse>(ENDPOINTS.PRICES, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    timeoutMs: SCANNER_LIVE_PRICE_TIMEOUT_MS,
    signal,
  });
}

/**
 * Fetch market context / briefing for the scanner dashboard.
 */
export async function fetchContext(
  universe: string,
  symbols: string[],
  signal?: AbortSignal,
): Promise<MarketScannerContextResponse> {
  return fetchJson<MarketScannerContextResponse>(ENDPOINTS.CONTEXT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      universe,
      symbols,
      leaders: [],
      limit_per_symbol: 3,
    }),
    timeoutMs: SCANNER_CONTEXT_TIMEOUT_MS,
    signal,
  });
}

// ── Data Transformation ─────────────────────────────────────────────────────

/**
 * Derives a phase label from the primary timeframe signal.
 *
 * Phase mapping logic (institutional convention):
 *   - Phase A: 5m, 15m (data ingestion / validation)
 *   - Phase B: 1h (microstructure)
 *   - Phase C: 1D (derivatives)
 *   - Phase D: N/A (execution — derived from Phase C output)
 */
function derivePhase(row: MarketScannerRow): "A" | "B" | "C" | "D" {
  const signalKeys = Object.keys(row.signals);
  if (signalKeys.includes("5m") || signalKeys.includes("15m")) {
    return "A";
  }
  if (signalKeys.includes("1h")) {
    return "B";
  }
  if (signalKeys.includes("1D")) {
    return "C";
  }
  return "A";
}

/**
 * Safely formats a number as a string with fixed decimals.
 * Returns "N/A" for null/undefined values.
 */
function fmt(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  return value.toFixed(decimals);
}

/**
 * Transforms a backend MarketScannerRow into a display-friendly ScannerTickerDisplay.
 *
 * Prices are strings (PD-2: never float for money in UI).
 */
export function rowToDisplay(row: MarketScannerRow): ScannerTickerDisplay {
  return {
    symbol: row.symbol,
    price: fmt(row.price),
    change_pct: fmt(row.change_pct),
    phase: derivePhase(row),
    scanner_score: fmt(row.scanner_score),
    setup_grade: row.setup_grade,
    direction: row.direction,
    intraday_score: fmt(row.intraday_score),
    swing_score: fmt(row.swing_score),
    regime_label: row.regime_label,
    sparkline: row.sparkline,
    reasons: row.reasons,
    warnings: row.warnings,
    vetoes: row.vetoes,
    funding_suitability: row.funding_suitability,
    conviction_score:
      row.conviction_score !== null ? fmt(row.conviction_score) : null,
    capacity_score:
      row.capacity_signals?.capacity_score !== undefined
        ? fmt(row.capacity_signals.capacity_score)
        : null,
    universe_rank: row.universe_rank,
  };
}

/**
 * Transforms a full scan response into display-friendly ticker list.
 */
export function scanResponseToDisplay(
  response: MarketScannerResponse,
): ScannerTickerDisplay[] {
  return response.rows.map(rowToDisplay);
}

// ── Adapter: ScannerTickerDisplay → Ticker (backward compatibility) ─────────

/**
 * Adapts a ScannerTickerDisplay (backend-derived) to the legacy Ticker type
 * so existing TickerRow, TickerModal, and PhaseDonut components keep working.
 *
 * Missing fields are derived from available data or filled with safe defaults.
 * Prices are parsed from strings back to numbers for legacy components.
 */
export function displayToTicker(
  display: ScannerTickerDisplay,
): import("@/types").Ticker {
  const price = parseFloat(display.price) || 0;
  const changePct = parseFloat(display.change_pct) || 0;
  const prevPrice = price / (1 + changePct / 100);

  return {
    symbol: display.symbol,
    price,
    priceChange: price - prevPrice,
    priceChangePct: changePct,
    volume: 0,
    avgVolume: 0,
    iv: 0,
    ivRank: 0,
    phase: display.phase,
    momentum: parseFloat(display.intraday_score) || 0,
    signals: display.reasons.slice(0, 6).map((reason) => ({
      name: reason,
      value: 0,
      direction:
        display.direction === "bullish"
          ? "BULL"
          : display.direction === "bearish"
            ? "BEAR"
            : "NEUTRAL",
      weight: 1,
    })),
    greeks: { delta: 0, gamma: 0, theta: 0, vega: 0 },
    candles: display.sparkline.map((close, i) => ({
      time: Math.floor(Date.now() / 1000) - (display.sparkline.length - i) * 60,
      open: close,
      high: close,
      low: close,
      close,
      volume: 0,
    })),
  };
}

/**
 * Adapts an array of ScannerTickerDisplay to legacy Ticker[].
 */
export function displayListToTickers(
  list: ScannerTickerDisplay[],
): import("@/types").Ticker[] {
  return list.map(displayToTicker);
}

// ── Default Request Factory ─────────────────────────────────────────────────

/**
 * Creates a default scan request for a given universe.
 * Used by the UI when no custom filters are applied.
 */
export function defaultScanRequest(
  universe: string = "wall_street",
): MarketScannerRequest {
  return buildScanRequest({
    universe,
    maxRows: 50,
    includeDeepMetrics: false,
    adaptiveWeighting: true,
  });
}

// ── Strategy Weights API ─────────────────────────────────────────────────────

/**
 * Flat weight dict keyed by dot-separated path (e.g. "phase_a.phase_weight").
 * Matches the backend `StrategyWeights.to_flat_dict()` format.
 */
export type FlatWeights = Record<string, number>;

/**
 * Fetch all active strategy weights from the backend.
 * Returns a flat dict of weight_path → value.
 */
export async function fetchStrategyWeights(
  signal?: AbortSignal,
): Promise<FlatWeights> {
  const res = await fetchJson<{ active_weights: FlatWeights }>(
    "/api/strategy/weights",
    { timeoutMs: SCANNER_API_TIMEOUT_MS, signal },
  );
  return res.active_weights;
}

/**
 * Update a single weight by dot-separated path.
 * @example updateStrategyWeight("phase_c.gex_score", 0.25)
 */
export async function updateStrategyWeight(
  path: string,
  value: number,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson(`/api/strategy/weights/${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
    timeoutMs: SCANNER_API_TIMEOUT_MS,
    signal,
  });
}

/**
 * Bulk-update strategy weights (deep merge into the backend store).
 */
export async function bulkUpdateStrategyWeights(
  weights: FlatWeights,
  signal?: AbortSignal,
): Promise<void> {
  await fetchJson("/api/strategy/weights", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(weights),
    timeoutMs: SCANNER_API_TIMEOUT_MS,
    signal,
  });
}

/**
 * Reset all strategy weights to server-side defaults.
 */
export async function resetStrategyWeights(
  signal?: AbortSignal,
): Promise<FlatWeights> {
  const res = await fetchJson<{ weights: FlatWeights }>(
    "/api/strategy/weights/reset",
    { method: "POST", timeoutMs: SCANNER_API_TIMEOUT_MS, signal },
  );
  return res.weights;
}
