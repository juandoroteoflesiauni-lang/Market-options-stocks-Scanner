"use client";

import * as React from "react";

import { fetchJson } from "@/lib/api-client";
import type {
  BingXAccountState,
  BingXAuditCycleSummary,
  BingXBotConfig,
  BingXBotStatus,
  BingXHealthcheck,
  BingXInstrument,
  BingXLiveReadiness,
  BingXTelemetry,
  BingXOperationLedgerRow,
  BingXScanResult,
} from "@/lib/bingx-bot-types";

const POLL_INTERVAL_MS = 30_000;
const ACCOUNT_POLL_INTERVAL_MS = 60_000;
const MAX_DYNAMIC_SCAN_SYMBOLS = 30;
const PRIORITY_SCAN_SYMBOLS = [
  "AAPL-USDT",
  "MSFT-USDT",
  "TSLA-USDT",
  "PLTR-USDT",
  "NVDA-USDT",
  "META-USDT",
  "GOOGL-USDT",
];

const DEFAULT_STATUS: BingXBotStatus = {
  connected: false,
  dry_run: true,
  balance_usdt: 10.0,
  universe: [],
  universe_details: [],
  account: null,
  positions: [],
  open_orders: [],
  last_cycle_at: null,
  snapshots: [],
  decisions: [],
  executions: [],
};

interface BingXUniverseResponse {
  universe: BingXInstrument[];
}

function requestScan(symbols: string[] | null) {
  return fetchJson<BingXScanResult>("/api/v1/bingx-bot/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols, scanner_confirmation: false }),
  });
}

function selectDynamicScanSymbols(
  activeUniverse: string[],
  priorityUniverse: string[],
) {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const symbol of [
    ...PRIORITY_SCAN_SYMBOLS,
    ...priorityUniverse,
    ...activeUniverse,
  ]) {
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    out.push(symbol);
    if (out.length >= MAX_DYNAMIC_SCAN_SYMBOLS) break;
  }
  return out;
}

export function useBingxBot() {
  const [status, setStatus] = React.useState<BingXBotStatus>(DEFAULT_STATUS);
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const universePromise = fetchJson<BingXUniverseResponse>(
        "/api/v1/bingx-bot/universe",
        {
          quiet: true,
        },
      );
      const [config, scan, account] = await Promise.all([
        fetchJson<BingXBotConfig>("/api/v1/bingx-bot/status"),
        requestScan(null),
        fetchJson<BingXAccountState>("/api/v1/bingx-bot/account", {
          quiet: true,
        }).catch(() => null),
      ]);
      setStatus((prev) => ({
        connected: true,
        dry_run: account?.dry_run ?? config.dry_run,
        balance_usdt: account?.total_equity_usdt ?? prev.balance_usdt,
        universe: config.universe,
        universe_details: prev.universe_details,
        account,
        positions: account?.open_positions ?? [],
        open_orders: account?.open_orders ?? [],
        last_cycle_at: scan.finished_at,
        snapshots: scan.snapshots,
        decisions: scan.decisions,
        executions: prev.executions, // accumulate across polls
      }));

      void universePromise
        .then(async (universeResult) => {
          const universeDetails = universeResult.universe;
          const activeUniverse = universeDetails.length
            ? universeDetails.map((item) => item.symbol)
            : config.universe;
          setStatus((prev) => ({
            ...prev,
            universe: activeUniverse,
            universe_details: universeDetails,
          }));

          const scanSymbols = selectDynamicScanSymbols(
            activeUniverse,
            config.universe,
          );
          if (scanSymbols.length === 0) return;
          const dynamicScan = await requestScan(scanSymbols);
          setStatus((prev) => ({
            ...prev,
            last_cycle_at: dynamicScan.finished_at,
            snapshots: dynamicScan.snapshots,
            decisions: dynamicScan.decisions,
          }));
        })
        .catch(() => {
          // The cockpit can operate with the static status universe.
        });
    } catch (cause) {
      const msg =
        cause instanceof Error ? cause.message : "BingX Bot unavailable";
      setError(msg);
      setStatus((prev) => ({ ...prev, connected: false }));
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    const id = setTimeout(() => void refresh(), 0);
    const intervalId = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      clearTimeout(id);
      clearInterval(intervalId);
    };
  }, [refresh]);

  return { status, isLoading, error, refresh };
}

const READINESS_POLL_INTERVAL_MS = 60_000;

interface CycleListResponse {
  cycles: BingXAuditCycleSummary[];
  count: number;
}

interface OperationListResponse {
  operations: BingXOperationLedgerRow[];
  count: number;
}

export function useProductionReadiness() {
  const [healthcheck, setHealthcheck] = React.useState<BingXHealthcheck | null>(
    null,
  );
  const [telemetry, setTelemetry] = React.useState<BingXTelemetry | null>(null);
  const [cycles, setCycles] = React.useState<BingXAuditCycleSummary[]>([]);
  const [operations, setOperations] = React.useState<BingXOperationLedgerRow[]>(
    [],
  );
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async (options?: { probe?: boolean }) => {
    setIsLoading(true);
    setError(null);
    const healthcheckEndpoint = options?.probe
      ? "/api/v1/bingx-bot/healthcheck?probe=true"
      : "/api/v1/bingx-bot/healthcheck";
    try {
      const [hc, lr, cl, ol] = await Promise.all([
        fetchJson<BingXHealthcheck>(healthcheckEndpoint, {
          quiet: true,
        }).catch(() => null),
        fetchJson<BingXTelemetry>("/api/v1/bingx-bot/telemetry", {
          quiet: true,
        }).catch(() => null),
        fetchJson<CycleListResponse>("/api/v1/bingx-bot/cycles?limit=10", {
          quiet: true,
        }).catch(() => null),
        fetchJson<OperationListResponse>(
          "/api/v1/bingx-bot/operations?limit=100",
          {
            quiet: true,
          },
        ).catch(() => null),
      ]);
      if (hc) setHealthcheck(hc);
      if (lr) setTelemetry(lr);
      if (cl) setCycles(cl.cycles);
      if (ol) setOperations(ol.operations);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Readiness check unavailable",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const runProbe = React.useCallback(async () => {
    await refresh({ probe: true });
  }, [refresh]);

  React.useEffect(() => {
    const id = setTimeout(() => void refresh(), 0);
    const intervalId = setInterval(
      () => void refresh(),
      READINESS_POLL_INTERVAL_MS,
    );
    return () => {
      clearTimeout(id);
      clearInterval(intervalId);
    };
  }, [refresh]);

  return {
    healthcheck,
    telemetry,
    cycles,
    operations,
    isLoading,
    error,
    refresh,
    runProbe,
  };
}

export function useAccountState() {
  const [account, setAccount] = React.useState<BingXAccountState | null>(null);
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchJson<BingXAccountState>(
        "/api/v1/bingx-bot/account",
      );
      setAccount(data);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Account state unavailable",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    const id = setTimeout(() => void refresh(), 0);
    const intervalId = setInterval(
      () => void refresh(),
      ACCOUNT_POLL_INTERVAL_MS,
    );
    return () => {
      clearTimeout(id);
      clearInterval(intervalId);
    };
  }, [refresh]);

  return { account, isLoading, error, refresh };
}
