"use client";

import { useSystemHealth } from "@/hooks/useSystemHealth";

import type { ProviderHealth, ProviderStatus } from "@/store/types";

const statusDotColor: Record<ProviderStatus, string> = {
  HEALTHY: "bg-signal-buy shadow-[0_0_6px_rgba(0,212,170,0.5)]",
  DEGRADED: "bg-signal-warning shadow-[0_0_6px_rgba(245,158,11,0.5)]",
  DOWN: "bg-signal-sell shadow-[0_0_6px_rgba(255,77,109,0.5)]",
};

function ProviderIndicator({ provider }: { provider: ProviderHealth }) {
  return (
    <div className="flex items-center gap-2">
      <div
        className={[
          "h-1.5 w-1.5 rounded-full",
          statusDotColor[provider.status],
        ].join(" ")}
        title={`${provider.name}: ${provider.status} (${provider.circuit_state})`}
      />
      <span className="text-text-secondary">{provider.name}</span>
      <span className="font-mono tabular-nums text-text-muted">
        {provider.latency_ms}ms
      </span>
    </div>
  );
}

function formatUptime(seconds: number): string {
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export function SystemStatusBar() {
  const { health, error } = useSystemHealth();

  if (error || !health) {
    return (
      <footer className="glass-panel px-6 py-2 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-1.5 rounded-full bg-signal-sell animate-pulse" />
          <span className="text-text-muted">
            {error ?? "Connecting to backend..."}
          </span>
        </div>
      </footer>
    );
  }

  return (
    <footer
      className="glass-panel px-6 py-2 flex items-center justify-between text-xs"
      role="contentinfo"
      aria-label="System status"
    >
      {/* Providers */}
      <div className="flex items-center gap-4">
        {health.providers.map((p) => (
          <ProviderIndicator key={p.name} provider={p} />
        ))}
      </div>

      {/* Queue metrics */}
      <div className="flex items-center gap-4 text-text-muted">
        <span>
          Q:{" "}
          <span className="font-mono tabular-nums text-text-secondary">
            {health.queues.standard_size}
          </span>
          /{health.queues.standard_max}
        </span>
        <span>
          PQ:{" "}
          <span className="font-mono tabular-nums text-text-secondary">
            {health.queues.priority_size}
          </span>
          /{health.queues.priority_max}
        </span>
      </div>

      {/* Uptime */}
      <div className="flex items-center gap-2">
        <span className="text-text-muted">Uptime</span>
        <span className="font-mono tabular-nums text-text-secondary">
          {formatUptime(health.uptime_seconds)}
        </span>
      </div>
    </footer>
  );
}
