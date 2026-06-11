"use client";
import { useEffect, useRef } from "react";
import { useTradingStore } from "@/store/tradingStore";
import { env } from "@/lib/env";
import type { Ticker } from "@/types";

export function useWebSocket(symbol: string = "ALL") {
  const setConnected = useTradingStore((s) => s.setConnected);
  const updateTicker = useTradingStore((s) => s.updateTicker);
  const retryCount = useRef(0);

  useEffect(() => {
    let ws: WebSocket;
    let timeoutId: NodeJS.Timeout;

    const connect = () => {
      // Connect to Phase D WebSocket stream
      let baseUrl = env.NEXT_PUBLIC_WS_URL;
      if (typeof window !== "undefined" && baseUrl.includes("localhost")) {
        baseUrl = baseUrl.replace("localhost", window.location.hostname);
      }
      const wsUrl = `${baseUrl}/stream/${symbol}`;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setConnected(true);
        retryCount.current = 0;
        console.log(`WS connected to ${wsUrl}`);
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          // Expecting data to have a symbol field for partial updates
          if (data.symbol) {
            // FIMA's Ticker interface expects numbers, but the backend sends Wall Street strings
            const parsedData: Partial<Ticker> & { symbol: string } = {
              symbol: data.symbol,
            };
            if (data.price !== undefined) parsedData.price = Number(data.price);
            if (data.priceChange !== undefined)
              parsedData.priceChange = Number(data.priceChange);
            if (data.priceChangePct !== undefined)
              parsedData.priceChangePct = Number(data.priceChangePct);
            if (data.afterMarketPrice !== undefined)
              parsedData.afterMarketPrice = Number(data.afterMarketPrice);
            if (data.afterMarketChangePct !== undefined)
              parsedData.afterMarketChangePct = Number(
                data.afterMarketChangePct,
              );
            if (data.candles !== undefined) parsedData.candles = data.candles;

            updateTicker(parsedData);
          }
        } catch (err) {
          console.error("WS parse error", err);
        }
      };

      ws.onerror = () => {
        console.warn(`WS error on ${wsUrl} (readyState: ${ws.readyState})`);
      };

      ws.onclose = () => {
        setConnected(false);
        const delay = Math.min(1000 * 2 ** retryCount.current, 30000); // Exponential backoff up to 30s
        retryCount.current++;
        console.log(`WS closed. Reconnecting in ${delay}ms...`);
        timeoutId = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      clearTimeout(timeoutId);
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.onopen = null;
        if (ws.readyState === WebSocket.CONNECTING) {
          ws.addEventListener("open", () => ws.close(), { once: true });
        } else {
          ws.close();
        }
      }
    };
  }, [symbol, setConnected, updateTicker]);
}
