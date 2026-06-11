/**
 * useScannerWebSocket — Real-time price updates for the Market Scanner.
 *
 * Connects to the backend WebSocket stream and updates the scanner store
 * with live price data. Uses exponential backoff for reconnection.
 *
 * The existing /ws/stream/{symbol} endpoint broadcasts price ticks.
 * Connecting to /ws/stream/ALL receives all symbol updates.
 *
 * @module hooks/useScannerWebSocket
 */

"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useScannerStore } from "@/store/scannerStore";
import { env } from "@/lib/env";

interface WsPriceMessage {
  symbol?: string;
  price?: string | number;
  priceChangePct?: string | number;
  priceChange?: string | number;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;

export interface UseScannerWebSocketReturn {
  /** Whether the WebSocket is currently connected. */
  isConnected: boolean;
  /** Manually reconnect. */
  reconnect: () => void;
  /** Manually disconnect. */
  disconnect: () => void;
}

export function useScannerWebSocket(): UseScannerWebSocketReturn {
  const updateTickerPrice = useScannerStore((s) => s.updateTickerPrice);
  const [isConnected, setIsConnected] = useState(false);
  const retryCountRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const cleanup = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (wsRef.current) {
      const ws = wsRef.current;
      ws.onclose = null;
      ws.onerror = null;
      ws.onopen = null;
      ws.onmessage = null;
      if (ws.readyState === WebSocket.CONNECTING) {
        ws.addEventListener("open", () => ws.close(), { once: true });
      } else {
        ws.close();
      }
      wsRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!mountedRef.current) return;

    const onMessage = (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data: WsPriceMessage = JSON.parse(e.data);
        if (!data.symbol) return;

        const price =
          typeof data.price === "string" ? parseFloat(data.price) : data.price;
        const changePct =
          typeof data.priceChangePct === "string"
            ? parseFloat(data.priceChangePct)
            : data.priceChangePct;

        if (price !== undefined && !Number.isNaN(price) && price > 0) {
          updateTickerPrice(
            data.symbol.toUpperCase(),
            price,
            changePct ?? null,
          );
        }
      } catch {
        // Ignore parse errors from non-JSON frames
      }
    };

    const connect = () => {
      if (!mountedRef.current) return;
      cleanup();

      const baseUrl = env.NEXT_PUBLIC_WS_URL;
      const wsUrl = `${baseUrl}/stream/ALL`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setIsConnected(true);
        retryCountRef.current = 0;
      };

      ws.onmessage = onMessage;

      ws.onerror = () => {
        // Error handling is done in onclose
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setIsConnected(false);
        wsRef.current = null;

        // Exponential backoff reconnection
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** retryCountRef.current,
          RECONNECT_MAX_MS,
        );
        retryCountRef.current++;

        timeoutRef.current = setTimeout(() => {
          connect();
        }, delay);
      };
    };

    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, [cleanup, updateTickerPrice]);

  const reconnect = useCallback(() => {
    retryCountRef.current = 0;
    // Trigger reconnect by updating a dependency
    mountedRef.current = true;
  }, []);

  const disconnect = useCallback(() => {
    cleanup();
  }, [cleanup]);

  return {
    isConnected,
    reconnect,
    disconnect,
  };
}
