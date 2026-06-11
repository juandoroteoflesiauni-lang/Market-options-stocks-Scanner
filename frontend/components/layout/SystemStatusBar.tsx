"use client";

import { useFunnelStore } from "@/store/funnelStore";
import { useEffect, useState } from "react";
import clsx from "clsx";

// Static mock ticker items for the scrolling tape, matching the screenshots
const tickerItems = [
  { symbol: "AMZN", change: "+0.87%", isPositive: true },
  { symbol: "SPY", change: "-0.64%", isPositive: false },
  { symbol: "QQQ", change: "-0.79%", isPositive: false },
  { symbol: "NFLX", change: "-1.48%", isPositive: false },
  { symbol: "AAPL", change: "-1.18%", isPositive: false },
  { symbol: "MSFT", change: "+0.33%", isPositive: true },
  { symbol: "TSLA", change: "-0.55%", isPositive: false },
  { symbol: "GOOGL", change: "-1.02%", isPositive: false },
  { symbol: "META", change: "-0.53%", isPositive: false },
  { symbol: "NVDA", change: "-0.15%", isPositive: false },
];

export function SystemStatusBar() {
  const isConnected = useFunnelStore((s) => s.isConnected);
  const systemHealth = useFunnelStore((s) => s.systemHealth);
  const [refreshTime, setRefreshTime] = useState(() =>
    new Date().toLocaleTimeString("en-US", { hour12: false }),
  );

  useEffect(() => {
    // Update refresh timestamp every 5 seconds
    const interval = setInterval(() => {
      setRefreshTime(new Date().toLocaleTimeString("en-US", { hour12: false }));
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Check provider health states or default to healthy (LIVE)
  const getProviderClass = (providerName: string) => {
    if (!isConnected) return "bg-data-negative";

    if (systemHealth?.providers) {
      const provider = systemHealth.providers.find(
        (p) => p.name.toUpperCase() === providerName.toUpperCase(),
      );
      if (provider) {
        if (provider.status === "HEALTHY") return "bg-data-positive";
        if (provider.status === "DEGRADED") return "bg-data-warning";
        return "bg-data-negative";
      }
    }
    return "bg-data-positive";
  };

  return (
    <footer className="fixed bottom-0 z-50 w-full h-6 bg-bg-inset border-t border-border-subtle flex items-center justify-between px-4 text-[10px] font-data text-text-muted select-none">
      {/* Left: Adapters Connection Status */}
      <div className="flex items-center gap-4 shrink-0">
        <div className="flex items-center gap-1.5">
          <span>BINGX</span>
          <span
            className={clsx(
              "w-1.5 h-1.5 rounded-full",
              getProviderClass("BINGX"),
            )}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span>ALPACA</span>
          <span
            className={clsx(
              "w-1.5 h-1.5 rounded-full",
              getProviderClass("ALPACA"),
            )}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span>BINANCE</span>
          <span
            className={clsx(
              "w-1.5 h-1.5 rounded-full",
              getProviderClass("BINANCE"),
            )}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span>OPTIONS FEED</span>
          <span
            className={clsx(
              "w-1.5 h-1.5 rounded-full",
              getProviderClass("OPTIONS FEED"),
            )}
          />
        </div>
      </div>

      {/* Middle: Horizontal Ticker Tape */}
      <div className="flex-1 mx-6 overflow-hidden relative h-full flex items-center">
        <div className="flex gap-6 whitespace-nowrap animate-[ticker-scroll_30s_linear_infinite] hover:[animation-play-state:paused]">
          {/* Double list to loop seamlessly */}
          {[...tickerItems, ...tickerItems].map((item, idx) => (
            <div key={idx} className="flex items-center gap-1">
              <span className="text-text-secondary">{item.symbol}</span>
              <span
                className={clsx(
                  item.isPositive ? "text-data-positive" : "text-data-negative",
                )}
              >
                {item.isPositive ? "▲" : "▼"} {item.change}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Right: Last refresh timestamp & System version */}
      <div className="flex items-center gap-4 shrink-0 font-data">
        <span>REFRESH {refreshTime}</span>
        <div className="w-px h-3 bg-border-subtle" />
        <span>FIMA v2.0.0</span>
      </div>
    </footer>
  );
}
