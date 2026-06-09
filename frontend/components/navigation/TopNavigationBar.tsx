"use client";

import { NavigationItem } from "./NavigationItem";
import { useFunnelStore } from "@/store/funnelStore";

const navItems = [
  { label: "Dashboard", href: "/" },
  { label: "Scanner", href: "/scanner" },
  { label: "Signals", href: "/signals" },
];

export function TopNavigationBar() {
  const isConnected = useFunnelStore((s) => s.isConnected);

  return (
    <header
      className={[
        "sticky top-0 z-50 w-full",
        "h-14 flex items-center justify-between px-6",
        "glass-panel",
      ].join(" ")}
      role="banner"
    >
      {/* Logo + Nav */}
      <div className="flex items-center gap-8">
        <span className="font-semibold text-lg tracking-tight text-text-primary">
          Deep Funnel Station
        </span>
        <nav className="hidden md:flex gap-6" aria-label="Main navigation">
          {navItems.map((item) => (
            <NavigationItem
              key={item.href}
              label={item.label}
              href={item.href}
            />
          ))}
        </nav>
      </div>

      {/* Status indicators */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <div
            className={[
              "h-2 w-2 rounded-full transition-colors duration-300",
              isConnected
                ? "bg-signal-buy shadow-[0_0_8px_rgba(0,212,170,0.6)]"
                : "bg-signal-sell shadow-[0_0_8px_rgba(255,77,109,0.4)]",
            ].join(" ")}
            title={isConnected ? "System Online" : "System Offline"}
          />
          <span className="hidden sm:inline">
            {isConnected ? "Online" : "Offline"}
          </span>
        </div>
      </div>
    </header>
  );
}
