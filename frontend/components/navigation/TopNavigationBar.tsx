"use client";

import { useFunnelStore, TabId } from "@/store/funnelStore";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { Settings } from "lucide-react";

const tabs: { id: TabId; label: string }[] = [
  { id: "SCANNER", label: "01 SCANNER" },
  { id: "BINGX", label: "02 BINGX" },
  { id: "ALPACA", label: "03 ALPACA" },
  { id: "BINANCE", label: "04 BINANCE" },
  { id: "FUNDING", label: "05 FUNDING" },
  { id: "DERIVATIVES", label: "06 DERIVADOS" },
  { id: "TECHNICAL", label: "07 TÉCNICO" },
  { id: "PREDICTIVE", label: "08 PREDICTIVO" },
];

export function TopNavigationBar() {
  const isConnected = useFunnelStore((s) => s.isConnected);
  const activeTab = useFunnelStore((s) => s.activeTab);
  const setActiveTab = useFunnelStore((s) => s.setActiveTab);

  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  return (
    <header className="fixed top-0 z-50 w-full h-[52px] bg-bg-void/80 backdrop-blur-md border-b border-white/5 flex items-center justify-between px-6 select-none">
      {/* Left: Brand logo (Toro dorado + GOKU STOCK ANALYZER ● LIVE) */}
      <div className="flex items-center gap-3">
        {/* Stylized Golden Bull Icon */}
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          className="text-amber-500 shrink-0"
        >
          <path
            d="M12 2L4 7V11C4 16.5 7.5 20 12 22C16.5 20 20 16.5 20 11V7L12 2Z"
            fill="currentColor"
            fillOpacity="0.12"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <path
            d="M9 9C9 9 10 11 12 11C14 11 15 9 15 9"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
          <path
            d="M7 6C8 7 10 7 12 7C14 7 16 7 17 6"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>

        <div className="flex items-baseline gap-2">
          <span className="font-display font-bold text-sm tracking-wide text-amber-500 uppercase">
            GOKU
          </span>
          <span className="font-display font-bold text-xs text-text-primary uppercase tracking-wider">
            STOCK ANALYZER
          </span>
          {/* Live indicator dot directly next to text */}
          <div className="flex items-center gap-1.5 ml-1">
            <span
              className={clsx(
                "w-1.5 h-1.5 rounded-full bg-data-positive",
                isConnected && "animate-pulse-dot",
              )}
            />
            <span className="text-[8px] font-mono text-data-positive font-bold uppercase tracking-widest">
              LIVE
            </span>
          </div>
        </div>
      </div>

      {/* Center: Tabs list matching capsule buttons */}
      <nav className="flex items-center gap-1">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                "px-3 py-1 font-display text-[11px] uppercase tracking-wider transition-all duration-120 cursor-pointer h-7 flex items-center rounded-full border",
                isActive
                  ? "bg-accent-muted text-accent-primary border-accent-primary/30 shadow-[0_2px_10px_rgba(0,195,255,0.1)] font-semibold"
                  : "bg-transparent text-text-secondary border-transparent hover:text-text-primary",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </nav>

      {/* Right: Clock, Status & Settings */}
      <div className="flex items-center gap-4 text-text-secondary">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-data-positive bg-data-positive/10 border border-data-positive/20 px-1.5 py-0.2 rounded-xs">
            OPEN
          </span>
          <span className="font-data text-xs text-text-primary w-[60px] text-right">
            {formatTime(time)}
          </span>
        </div>
        <div className="w-px h-4 bg-border-subtle" />
        <span className="font-data text-[10px] text-text-muted">v2.0.0</span>
        <button className="text-text-muted hover:text-text-primary transition-colors cursor-pointer">
          <Settings className="w-3.5 h-3.5" />
        </button>
      </div>
    </header>
  );
}
