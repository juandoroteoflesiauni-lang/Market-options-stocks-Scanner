"use client";

import * as React from "react";
import { fetchJson } from "@/lib/api-client";
import type {
  AlpacaBalance,
  AlpacaPosition,
  AlpacaStatusResponse,
  EquityCycleResult,
  MarketSession,
} from "@/types/alpaca";

const POLL_INTERVAL_MS = 30_000;

export interface AlpacaBotState {
  connected: boolean;
  dryRun: boolean;
  tradingMode: string;
  isLive: boolean;
  tradingEnvironment: string;
  balance: AlpacaBalance | null;
  universe: string[];
  positions: AlpacaPosition[];
  lastCycle: EquityCycleResult | null;
  lastCycleAt: string | null;
}

const DEFAULT_STATE: AlpacaBotState = {
  connected: false,
  dryRun: true,
  tradingMode: "paper",
  isLive: false,
  tradingEnvironment: "paper",
  balance: null,
  universe: [],
  positions: [],
  lastCycle: null,
  lastCycleAt: null,
};

/** Derive the US equity market session from the current UTC time. */
export function deriveSession(now: Date = new Date()): MarketSession {
  const day = now.getUTCDay();
  if (day === 0 || day === 6) return "CLOSED";
  const minutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  const preOpen = 8 * 60; // 04:00 ET
  const open = 13 * 60 + 30; // 09:30 ET
  const close = 20 * 60; // 16:00 ET
  const afterClose = 24 * 60; // 20:00 ET
  if (minutes >= open && minutes < close) return "OPEN";
  if (minutes >= preOpen && minutes < open) return "PRE";
  if (minutes >= close && minutes < afterClose) return "AFTER";
  return "CLOSED";
}

function toNumber(value: string | undefined | null): number {
  const parsed = Number.parseFloat(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export interface UseAlpacaBot {
  state: AlpacaBotState;
  isLoading: boolean;
  isCycling: boolean;
  error: string | null;
  session: MarketSession;
  equity: number;
  buyingPower: number;
  refresh: () => Promise<void>;
  runCycle: (allowLive?: boolean) => Promise<EquityCycleResult | null>;
}

export function useAlpacaBot(): UseAlpacaBot {
  const [state, setState] = React.useState<AlpacaBotState>(DEFAULT_STATE);
  const [isLoading, setIsLoading] = React.useState(false);
  const [isCycling, setIsCycling] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [status, positions] = await Promise.all([
        fetchJson<AlpacaStatusResponse>("/api/v1/alpaca-bot/status"),
        fetchJson<AlpacaPosition[]>("/api/v1/alpaca-bot/positions").catch(
          () => [] as AlpacaPosition[],
        ),
      ]);
      setState((prev) => ({
        ...prev,
        connected: true,
        dryRun: status.dry_run,
        tradingMode: status.trading_mode ?? status.trading_environment,
        isLive: status.is_live ?? false,
        tradingEnvironment: status.trading_environment,
        balance: status.balance ?? null,
        universe: status.universe ?? [],
        positions,
      }));
    } catch (cause) {
      const message =
        cause instanceof Error ? cause.message : "Alpaca Bot unavailable";
      setError(message);
      setState((prev) => ({ ...prev, connected: false }));
    } finally {
      setIsLoading(false);
    }
  }, []);

  const runCycle = React.useCallback(
    async (allowLive = false): Promise<EquityCycleResult | null> => {
      setIsCycling(true);
      setError(null);
      try {
        const result = await fetchJson<EquityCycleResult>(
          "/api/v1/alpaca-bot/cycle",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ allow_live: allowLive }),
          },
        );
        setState((prev) => ({
          ...prev,
          lastCycle: result,
          lastCycleAt: result.finished_at,
        }));
        await refresh();
        return result;
      } catch (cause) {
        const message =
          cause instanceof Error ? cause.message : "Alpaca Bot cycle failed";
        setError(message);
        return null;
      } finally {
        setIsCycling(false);
      }
    },
    [refresh],
  );

  React.useEffect(() => {
    const id = setTimeout(() => void refresh(), 0);
    const intervalId = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      clearTimeout(id);
      clearInterval(intervalId);
    };
  }, [refresh]);

  const equity = toNumber(state.balance?.equity);
  const buyingPower = toNumber(state.balance?.buying_power);
  const session = deriveSession();

  return {
    state,
    isLoading,
    isCycling,
    error,
    session,
    equity,
    buyingPower,
    refresh,
    runCycle,
  };
}
