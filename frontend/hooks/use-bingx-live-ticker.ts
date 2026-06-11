"use client";

import * as React from "react";

import { toWsUrl } from "@/lib/api-client";
import type {
  BingXTelemetryAccount,
  BingXTelemetryPosition,
} from "@/lib/bingx-bot-types";

export type BingxLiveTickerMessage = {
  type: "snapshot" | "tick" | string;
  captured_at?: string;
  venue_connected?: boolean;
  account?: BingXTelemetryAccount;
  positions?: BingXTelemetryPosition[];
};

export type BingxLiveTickerState = {
  account: BingXTelemetryAccount | null;
  positions: BingXTelemetryPosition[];
  connected: boolean;
  lastTickAt: Date | null;
  error: string | null;
};

const DEFAULT_ACCOUNT: BingXTelemetryAccount = {
  total_equity: 0,
  available_margin: 0,
  used_margin: 0,
};

export function useBingxLiveTicker(enabled = true) {
  const [state, setState] = React.useState<BingxLiveTickerState>({
    account: null,
    positions: [],
    connected: false,
    lastTickAt: null,
    error: null,
  });
  const wsRef = React.useRef<WebSocket | null>(null);
  const reconnectRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const applyMessage = React.useCallback((msg: BingxLiveTickerMessage) => {
    setState((prev) => ({
      account: msg.account ?? prev.account,
      positions: msg.positions ?? prev.positions,
      connected: true,
      lastTickAt: new Date(),
      error: null,
    }));
  }, []);

  React.useEffect(() => {
    if (!enabled) return undefined;

    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      const socket = new WebSocket(toWsUrl("/api/v1/ws/live-ticker"));
      wsRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        setState((prev) => ({ ...prev, connected: true, error: null }));
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        try {
          const payload = JSON.parse(
            String(event.data),
          ) as BingxLiveTickerMessage;
          applyMessage(payload);
        } catch {
          setState((prev) => ({
            ...prev,
            error: "Invalid live-ticker payload",
          }));
        }
      };

      socket.onerror = () => {
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          connected: false,
          error: "Live ticker WebSocket error",
        }));
      };

      socket.onclose = () => {
        if (cancelled) return;
        setState((prev) => ({ ...prev, connected: false }));
        reconnectRef.current = setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [enabled, applyMessage]);

  const account = state.account ?? DEFAULT_ACCOUNT;

  const positionsBySymbol = React.useMemo(() => {
    const map = new Map<string, BingXTelemetryPosition>();
    for (const row of state.positions) {
      map.set(row.symbol, row);
    }
    return map;
  }, [state.positions]);

  return {
    account,
    positions: state.positions,
    positionsBySymbol,
    connected: state.connected,
    lastTickAt: state.lastTickAt,
    error: state.error,
  };
}
