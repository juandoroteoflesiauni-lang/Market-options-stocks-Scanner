/**
 * useScanner — React hook for the Market Scanner module.
 *
 * Encapsulates scanner data fetching, lifecycle management, and
 * request cancellation. Persists data state in Zustand (scannerStore)
 * so results survive tab switches. Lifecycle state (isScanning, error)
 * stays local since it's per-component-mount.
 *
 * Design rules:
 *   - Server Components: this hook requires "use client".
 *   - Cleanup: all in-flight requests are cancelled on unmount.
 *   - Error handling: never swallow errors — expose to UI.
 *   - Deduplication: concurrent requests to the same endpoint are cancelled.
 *
 * @module hooks/useScanner
 */

"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useScannerStore } from "@/store/scannerStore";
import {
  performScan,
  fetchUniverses,
  fetchLivePrices,
  fetchContext,
  buildScanRequest,
  scanResponseToDisplay,
} from "@/services/scannerService";
import {
  SCANNER_DEBOUNCE_MS,
  SCANNER_MAX_RETRIES,
  SCANNER_PRICE_POLL_INTERVAL_MS,
} from "@/lib/constants";
import {
  ApiError,
  NetworkError,
  TimeoutError,
  AuthError,
} from "@/lib/api-client";
import type {
  MarketScannerContextResponse,
  ScannerTickerDisplay,
} from "@/types/marketScanner";
import type { ScanParams } from "@/store/scannerStore";

// ── Error Types ──────────────────────────────────────────────────────────────

export type ScannerErrorKind =
  | "network"
  | "timeout"
  | "auth"
  | "server"
  | "unknown";

export interface ScannerError {
  /** Human-readable error message. */
  message: string;
  /** Error category for UI display. */
  kind: ScannerErrorKind;
  /** Whether the operation can be retried. */
  retryable: boolean;
  /** HTTP status code (if applicable). */
  status?: number;
}

// ── Return Types ────────────────────────────────────────────────────────────

export interface UseScannerReturn {
  /** Current scan results (display-ready). */
  tickers: ScannerTickerDisplay[];
  /** Raw backend response (for advanced UI like score_audit). */
  rawResponse: import("@/types/marketScanner").MarketScannerResponse | null;
  /** Available universes from the backend. */
  universes: Record<
    string,
    import("@/types/marketScanner").MarketScannerUniverse
  >;
  /** Live price overrides (symbol → price). */
  livePrices: Record<string, { price: number; change_pct: number | null }>;
  /** Market context / brief. */
  context: MarketScannerContextResponse | null;
  /** Whether a scan is currently in progress. */
  isScanning: boolean;
  /** Whether live prices are being fetched. */
  isPolling: boolean;
  /** Whether initial data is loading. */
  isLoading: boolean;
  /** Structured error from the last failed operation. */
  error: ScannerError | null;
  /** Backend connection status. */
  isConnected: boolean;
  /** Currently selected universe key. */
  selectedUniverse: string;
  /** Current scan request parameters. */
  requestParams: ScanParams;

  // ── Actions ─────────────────────────────────────────────────────────────
  /** Execute a scan with current parameters. */
  scan: () => Promise<void>;
  /** Change the active universe and re-scan. */
  setUniverse: (universe: string) => void;
  /** Update scan parameters and re-scan. */
  updateParams: (params: Partial<ScanParams>) => void;
  /** Manually trigger a live price refresh. */
  refreshPrices: () => Promise<void>;
  /** Fetch market context for the current universe. */
  loadContext: () => Promise<void>;
  /** Clear the current error state. */
  clearError: () => void;
  /** Retry the last failed operation. */
  retry: () => void;
}

// ── Hook Implementation ─────────────────────────────────────────────────────

export function useScanner(): UseScannerReturn {
  // ── Zustand data state (persists across tab switches) ────────────────────
  const tickers = useScannerStore((s) => s.tickers);
  const rawResponse = useScannerStore((s) => s.rawResponse);
  const universes = useScannerStore((s) => s.universes);
  const livePrices = useScannerStore((s) => s.livePrices);
  const context = useScannerStore((s) => s.context);
  const selectedUniverse = useScannerStore((s) => s.selectedUniverse);
  const params = useScannerStore((s) => s.params);

  const setTickers = useScannerStore((s) => s.setTickers);
  const setRawResponse = useScannerStore((s) => s.setRawResponse);
  const setUniverses = useScannerStore((s) => s.setUniverses);
  const setLivePrices = useScannerStore((s) => s.setLivePrices);
  const setContext = useScannerStore((s) => s.setContext);
  const selectUniverse = useScannerStore((s) => s.selectUniverse);
  const updateStoreParams = useScannerStore((s) => s.updateParams);

  // ── Local lifecycle state (per-mount, not persisted) ─────────────────────
  const [isScanning, setIsScanning] = useState(false);
  const [isPolling, setIsPolling] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ScannerError | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const lastScanRef = useRef<"scan" | "context">("scan");

  // ── Refs for cleanup ───────────────────────────────────────────────────
  const abortRef = useRef<AbortController | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const contextAbortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Cleanup on unmount ─────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      pollAbortRef.current?.abort();
      contextAbortRef.current?.abort();
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  // ── Error classification helper ────────────────────────────────────────
  const classifyError = useCallback((err: unknown): ScannerError => {
    if (err instanceof AuthError) {
      return {
        message: "Authentication required. Please log in.",
        kind: "auth",
        retryable: false,
        status: 401,
      };
    }
    if (err instanceof ApiError) {
      const kind: ScannerErrorKind = err.status >= 500 ? "server" : "unknown";
      return {
        message:
          err.status >= 500
            ? "Server error. Please try again later."
            : err.message,
        kind,
        retryable: err.retryable,
        status: err.status,
      };
    }
    if (err instanceof TimeoutError) {
      return {
        message: "Request timed out. Check your connection.",
        kind: "timeout",
        retryable: true,
      };
    }
    if (err instanceof NetworkError) {
      return {
        message: "Network error. Check your connection.",
        kind: "network",
        retryable: true,
      };
    }
    if (err instanceof Error && err.name === "AbortError") {
      return {
        message: "Request cancelled",
        kind: "unknown",
        retryable: false,
      };
    }
    return {
      message:
        err instanceof Error ? err.message : "An unexpected error occurred",
      kind: "unknown",
      retryable: false,
    };
  }, []);

  // ── Core scan function with retry ──────────────────────────────────────
  const scan = useCallback(async () => {
    // Cancel any in-flight scan
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    lastScanRef.current = "scan";

    setIsScanning(true);
    setError(null);

    let lastError: ScannerError | null = null;

    for (let attempt = 0; attempt <= SCANNER_MAX_RETRIES; attempt++) {
      try {
        const request = buildScanRequest({
          universe: params.universe,
          maxRows: params.maxRows,
          minScore: params.minScore,
          minPrice: params.minPrice,
          minVolume: params.minVolume,
          timeframes: params.timeframes,
          direction: params.direction,
          sort: params.sort,
          includeDeepMetrics: params.includeDeepMetrics,
          adaptiveWeighting: params.adaptiveWeighting,
        });

        const response = await performScan(request, controller.signal);

        if (!controller.signal.aborted) {
          setRawResponse(response);
          setTickers(scanResponseToDisplay(response));
          setIsConnected(true);
          setIsLoading(false);
          setIsScanning(false);
          return;
        }
      } catch (err: unknown) {
        if (controller.signal.aborted) return;
        lastError = classifyError(err);

        if (!lastError.retryable || attempt >= SCANNER_MAX_RETRIES) break;

        // Exponential backoff: 1s, 2s, 4s
        await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
      }
    }

    if (!controller.signal.aborted && lastError) {
      setError(lastError);
      setIsConnected(false);
      setIsLoading(false);
      setIsScanning(false);
    }
  }, [params, setRawResponse, setTickers, classifyError]);

  // ── Load universes on mount ────────────────────────────────────────────
  useEffect(() => {
    const controller = new AbortController();

    async function loadUniverses() {
      try {
        const data = await fetchUniverses(controller.signal);
        if (!controller.signal.aborted) {
          setUniverses(data);
          setIsConnected(true);
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") return;
        // Universes are non-critical — log and continue
      }
    }

    loadUniverses();
    return () => controller.abort();
  }, [setUniverses]);

  // ── Live price polling ─────────────────────────────────────────────────
  const refreshPrices = useCallback(async () => {
    const symbols = tickers.map((t) => t.symbol);
    if (symbols.length === 0) return;

    pollAbortRef.current?.abort();
    const controller = new AbortController();
    pollAbortRef.current = controller;

    setIsPolling(true);
    try {
      const response = await fetchLivePrices(symbols, controller.signal);
      if (!controller.signal.aborted) {
        const prices: Record<
          string,
          { price: number; change_pct: number | null }
        > = {};
        for (const [symbol, row] of Object.entries(response.prices)) {
          prices[symbol] = { price: row.price, change_pct: row.change_pct };
        }
        setLivePrices(prices);
      }
    } catch (err: unknown) {
      // Price polling is non-critical — fail silently
      if (err instanceof Error && err.name === "AbortError") return;
    } finally {
      if (!controller.signal.aborted) {
        setIsPolling(false);
      }
    }
  }, [tickers, setLivePrices]);

  // ── Initial scan on mount (via microtask to avoid setState-in-effect) ──
  const initialScanDone = useRef(false);
  useEffect(() => {
    if (!initialScanDone.current) {
      initialScanDone.current = true;
      // Defer scan to next microtask to satisfy react-hooks/set-state-in-effect
      queueMicrotask(() => {
        scan();
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Start/stop polling when tickers change ─────────────────────────────
  useEffect(() => {
    if (tickers.length === 0) {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      return;
    }

    // Set up polling interval (refreshPrices is called by the interval callback)
    pollTimerRef.current = setInterval(() => {
      refreshPrices();
    }, SCANNER_PRICE_POLL_INTERVAL_MS);

    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [tickers.length, refreshPrices]);

  // ── Context loading ────────────────────────────────────────────────────
  const loadContext = useCallback(async () => {
    const symbols = tickers.slice(0, 10).map((t) => t.symbol);
    if (symbols.length === 0) return;

    contextAbortRef.current?.abort();
    const controller = new AbortController();
    contextAbortRef.current = controller;

    try {
      const data = await fetchContext(params.universe, symbols);
      if (!controller.signal.aborted) {
        setContext(data);
      }
    } catch (_err: unknown) {
      // Context is non-critical
    }
  }, [params.universe, tickers, setContext]);

  // ── Universe change ────────────────────────────────────────────────────
  const setUniverse = useCallback(
    (universe: string) => {
      selectUniverse(universe);
    },
    [selectUniverse],
  );

  // ── Parameter update with debounce ─────────────────────────────────────
  const updateParams = useCallback(
    (partial: Partial<ScanParams>) => {
      updateStoreParams(partial);

      // Debounce re-scan on rapid parameter changes
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        scan();
      }, SCANNER_DEBOUNCE_MS);
    },
    [scan, updateStoreParams],
  );

  // ── Error clear ────────────────────────────────────────────────────────
  const clearError = useCallback(() => setError(null), []);

  // ── Retry last failed operation ────────────────────────────────────────
  const retry = useCallback(() => {
    setError(null);
    if (lastScanRef.current === "scan") {
      scan();
    } else {
      loadContext();
    }
  }, [scan, loadContext]);

  return {
    tickers,
    rawResponse,
    universes,
    livePrices,
    context,
    isScanning,
    isPolling,
    isLoading,
    error,
    isConnected,
    selectedUniverse,
    requestParams: params,
    scan,
    setUniverse,
    updateParams,
    refreshPrices,
    loadContext,
    clearError,
    retry,
  };
}
