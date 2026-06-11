"use client";
import type { ReactNode } from "react";
import { Header } from "./Header";
import { StatusBar } from "./StatusBar";
import { useTabStore } from "@/store/tabStore";

interface Props {
  children: ReactNode;
}

export function Shell({ children }: Props) {
  const { activeTab } = useTabStore();
  const isTechnical = activeTab === "technical";

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-void)" }}>
      <Header />

      {/* Content area: between 52px header and 28px status bar */}
      <main
        className={isTechnical ? "" : "overflow-y-auto"}
        style={{
          position: "fixed",
          top: 52,
          left: 0,
          right: 0,
          bottom: 28,
          padding: isTechnical ? 0 : 16,
        }}
      >
        {children}
      </main>

      <StatusBar />

      {/* Mobile warning overlay */}
      <div
        className="fixed inset-0 z-[9998] flex items-center justify-center"
        style={{
          background: "var(--bg-void)",
          display: "none",
        }}
        id="mobile-overlay"
      >
        <div className="text-center px-8">
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 24,
              color: "#E8EDF5",
              marginBottom: 12,
            }}
          >
            Best viewed on desktop
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              color: "#8B9AAF",
            }}
          >
            Minimum width: 1280px
          </div>
        </div>
      </div>

      <style>{`
        @media (max-width: 900px) {
          #mobile-overlay { display: flex !important; }
        }
      `}</style>
    </div>
  );
}
