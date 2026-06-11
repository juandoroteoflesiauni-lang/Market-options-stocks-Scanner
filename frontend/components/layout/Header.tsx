"use client";
import { useEffect, useState } from "react";
import Image from "next/image";
import { Settings } from "lucide-react";
import { BreadthBar } from "./BreadthBar";
import { TabBar } from "./TabBar";
import { formatTime } from "@/utils/format";

function getMarketSession(): { label: string; color: string } {
  const now = new Date();
  const h = now.getUTCHours();
  const m = now.getUTCMinutes();
  const totalMin = h * 60 + m;
  // NYSE hours in UTC: pre 9:00–13:30, open 13:30–20:00, after 20:00–24:00
  if (totalMin >= 9 * 60 && totalMin < 13 * 60 + 30)
    return { label: "PRE-MKT", color: "#FFB800" };
  if (totalMin >= 13 * 60 + 30 && totalMin < 20 * 60)
    return { label: "OPEN", color: "#00E676" };
  if (totalMin >= 20 * 60 && totalMin < 24 * 60)
    return { label: "AFTER", color: "#FFB800" };
  return { label: "CLOSED", color: "#FF3D5A" };
}

export function Header() {
  const [time, setTime] = useState(new Date());
  const [session, setSession] = useState(getMarketSession());

  useEffect(() => {
    const id = setInterval(() => {
      const now = new Date();
      setTime(now);
      setSession(getMarketSession());
    }, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header
      className="fixed top-0 left-0 right-0 z-50 flex items-center px-4 gap-4"
      style={{
        height: 52,
        background: "rgba(8,12,20,0.95)",
        backdropFilter: "blur(12px)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      {/* Wordmark */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Wall Street Charging Bull icon */}
        <div
          style={{
            width: 32,
            height: 32,
            flexShrink: 0,
            overflow: "hidden",
            borderRadius: "4px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            mixBlendMode: "screen",
          }}
        >
          <Image
            src="/bull-logo.png"
            alt="Wall Street Bull"
            width={24}
            height={24}
            style={{
              width: "120%",
              height: "120%",
              objectFit: "cover",
              filter: "invert(1) contrast(1.2)",
            }}
          />
        </div>
        <span
          style={{
            fontFamily: "var(--font-display)",
            color: "#E8EDF5",
            fontSize: 15,
            fontWeight: 600,
            letterSpacing: "0.06em",
          }}
        >
          GOKU STOCK ANALYZER
        </span>

        {/* LIVE dot */}
        <div className="flex items-center gap-1 ml-2">
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{
              background: "#00E676",
              animation: "pulse-green 2s ease-in-out infinite",
            }}
          />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#00E676",
              letterSpacing: "0.1em",
            }}
          >
            LIVE
          </span>
        </div>
      </div>

      {/* Tab bar — grows to fill */}
      <div className="flex-1 flex items-center justify-center overflow-x-auto">
        <TabBar />
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-3 shrink-0">
        {/* Market Breadth */}
        <BreadthBar />

        {/* Market session */}
        <div
          className="px-2 py-0.5 rounded"
          style={{
            border: `1px solid ${session.color}33`,
            background: `${session.color}11`,
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: session.color,
              letterSpacing: "0.1em",
            }}
          >
            {session.label}
          </span>
        </div>

        {/* Clock */}
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            color: "#E8EDF5",
            letterSpacing: "0.05em",
          }}
        >
          {formatTime(time)}
        </span>

        {/* Session badge */}
        <div
          className="px-2 py-0.5 rounded"
          style={{
            background: "#1E2D47",
            border: "1px solid rgba(0,195,255,0.20)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "#00C3FF",
              letterSpacing: "0.08em",
            }}
          >
            v2.0.0
          </span>
        </div>

        {/* Settings */}
        <button
          className="flex items-center justify-center w-7 h-7 rounded transition-colors duration-150"
          style={{ color: "#8B9AAF" }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "#00C3FF")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "#8B9AAF")}
        >
          <Settings size={14} />
        </button>
      </div>
    </header>
  );
}
