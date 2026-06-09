"use client";

import { useEffect, useRef, useCallback } from "react";

import { buildWsUrl } from "@/lib/api";
import { useFunnelStore } from "@/store/funnelStore";

import type { ExecutionSignal } from "@/store/types";

const MAX_RECONNECT_DELAY_MS = 30_000;
const BASE_RECONNECT_DELAY_MS = 1_000;

/**
 * WebSocket hook for real-time ExecutionSignal streaming.
 *
 * Lifecycle: connect → receive signals → update store → cleanup on unmount.
 * Reconnect: exponential backoff with jitter, max 30s.
 * Per CLAUDE.md WebSocket lifecycle requirements.
 */
export function useSignalStream(): void {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const retryCountRef = useRef(0);
  const connectRef = useRef<() => void>(() => {});

  const addSignal = useFunnelStore((s) => s.addSignal);
  const setConnected = useFunnelStore((s) => s.setConnected);

  const connect = useCallback(() => {
    // Clean up any existing connection
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.close();
    }

    const ws = new WebSocket(buildWsUrl("/signals"));
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retryCountRef.current = 0;
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const signal = JSON.parse(event.data as string) as ExecutionSignal;
        addSignal(signal);
      } catch (err) {
        console.error("Failed to parse signal:", err);
      }
    };

    ws.onerror = () => {
      console.error("WebSocket error on signal stream");
    };

    ws.onclose = (event: CloseEvent) => {
      setConnected(false);

      // Only reconnect on unexpected closure
      if (event.code !== 1000) {
        const delay = Math.min(
          BASE_RECONNECT_DELAY_MS * Math.pow(2, retryCountRef.current),
          MAX_RECONNECT_DELAY_MS,
        );
        // Jitter to prevent thundering herd
        const jitter = Math.random() * 1000;
        retryCountRef.current += 1;
        reconnectTimeoutRef.current = setTimeout(() => {
          connectRef.current();
        }, delay + jitter);
      }
    };
  }, [addSignal, setConnected]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    connect();

    // CRITICAL: cleanup on unmount — per CLAUDE.md WebSocket rules
    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close(1000, "Component unmounted");
      wsRef.current = null;
    };
  }, [connect]);
}
