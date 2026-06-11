"use client";

import * as React from "react";
import { Sliders, ChevronRight, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AuditDashboard, ModuleDetail } from "@/hooks/use-audit-complex";

interface Props {
  dashboard: AuditDashboard | null;
  fetchModuleDetail: (module: string) => Promise<ModuleDetail>;
}

export function ProcessAuditTab({ dashboard, fetchModuleDetail }: Props) {
  const [selectedModule, setSelectedModule] = React.useState<string | null>(
    null,
  );
  const [detail, setDetail] = React.useState<ModuleDetail | null>(null);
  const [isLoading, setIsLoading] = React.useState(false);

  const modules = React.useMemo(() => {
    if (!dashboard?.module_summary) return [];
    return Object.keys(dashboard.module_summary).sort();
  }, [dashboard?.module_summary]);

  const loadDetail = React.useCallback(
    async (mod: string) => {
      setSelectedModule(mod);
      setIsLoading(true);
      try {
        const d = await fetchModuleDetail(mod);
        setDetail(d);
      } catch {
        setDetail(null);
      } finally {
        setIsLoading(false);
      }
    },
    [fetchModuleDetail],
  );

  return (
    <div className="space-y-4">
      {/* Module cards */}
      <section className="bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden">
        <div className="p-4 border-b border-white/5">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
            <Sliders className="h-3.5 w-3.5" />
            Módulos del Sistema
          </h3>
        </div>

        {modules.length === 0 ? (
          <div className="p-6 text-center text-[10px] font-mono text-zinc-600">
            Sin datos de módulos.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-4">
            {modules.map((mod) => {
              const summary = dashboard!.module_summary[mod];
              const apiStats = dashboard!.api_call_stats[mod];
              const errStats = dashboard!.error_stats[mod];
              const isSelected = selectedModule === mod;

              const healthScore = computeHealthScore(
                apiStats?.error_rate_pct ?? 0,
                errStats?.unresolved ?? 0,
              );
              const healthColor =
                healthScore > 80
                  ? "text-emerald-400"
                  : healthScore > 50
                    ? "text-amber-400"
                    : "text-rose-400";

              return (
                <div
                  key={mod}
                  onClick={() => void loadDetail(mod)}
                  className={cn(
                    "border rounded-xl p-4 cursor-pointer transition-all hover:border-white/10",
                    isSelected
                      ? "border-[rgba(0,195,255,0.30)] bg-white/[0.03]"
                      : "border-white/5 bg-[#18181b]/20",
                  )}
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-mono text-xs font-bold text-zinc-200 uppercase">
                      {mod}
                    </span>
                    <span
                      className={cn("font-mono text-lg font-bold", healthColor)}
                    >
                      {healthScore}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-[9px] font-mono">
                    <div>
                      <span className="text-zinc-500">API Calls</span>
                      <div className="text-zinc-200 font-bold">
                        {summary.api_calls.toLocaleString()}
                      </div>
                    </div>
                    <div>
                      <span className="text-zinc-500">Costo</span>
                      <div className="text-emerald-400 font-bold">
                        ${summary.api_cost_usd.toFixed(4)}
                      </div>
                    </div>
                    <div>
                      <span className="text-zinc-500">Errores</span>
                      <div
                        className={cn(
                          "font-bold",
                          summary.errors_unresolved > 0
                            ? "text-rose-400"
                            : "text-zinc-400",
                        )}
                      >
                        {summary.errors_total} ({summary.errors_unresolved}{" "}
                        abiertos)
                      </div>
                    </div>
                    <div>
                      <span className="text-zinc-500">Error Rate</span>
                      <div
                        className={cn(
                          "font-bold",
                          summary.api_error_rate_pct > 5
                            ? "text-rose-400"
                            : "text-zinc-400",
                        )}
                      >
                        {summary.api_error_rate_pct.toFixed(1)}%
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Detail drawer */}
      {selectedModule && detail && (
        <section className="bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden">
          <div className="p-4 border-b border-white/5 flex items-center justify-between">
            <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
              Detalle: <span className="text-amber-400">{selectedModule}</span>
            </h3>
            <button
              onClick={() => {
                setSelectedModule(null);
                setDetail(null);
              }}
              className="p-1 text-zinc-500 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="p-4 space-y-4">
            {/* Recent API calls */}
            {detail.api_calls.recent.length > 0 && (
              <div>
                <h4 className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-2">
                  Últimas API Calls
                </h4>
                <div className="space-y-1">
                  {detail.api_calls.recent.map((call) => (
                    <div
                      key={call.call_id}
                      className="flex items-center justify-between border border-white/5 bg-[#18181b]/10 p-2 rounded text-[10px] font-mono"
                    >
                      <span className="text-zinc-400 truncate max-w-[200px]">
                        {call.endpoint}
                      </span>
                      <span
                        className={cn(
                          "font-bold",
                          call.status === "success"
                            ? "text-emerald-400"
                            : "text-rose-400",
                        )}
                      >
                        {call.status}
                      </span>
                      <span className="text-zinc-500">
                        {call.duration_ms.toFixed(0)}ms
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent errors */}
            {detail.errors.recent.length > 0 && (
              <div>
                <h4 className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-2">
                  Últimos Errores
                </h4>
                <div className="space-y-1">
                  {detail.errors.recent.map((err) => (
                    <div
                      key={err.error_id}
                      className="flex items-center justify-between border border-rose-500/10 bg-rose-500/5 p-2 rounded text-[10px] font-mono"
                    >
                      <span
                        className={cn(
                          "font-bold uppercase",
                          err.severity === "critical"
                            ? "text-red-500"
                            : "text-rose-400",
                        )}
                      >
                        {err.severity}
                      </span>
                      <span className="text-zinc-300 truncate max-w-[300px]">
                        {err.message}
                      </span>
                      <span className="text-zinc-600">
                        {new Date(err.timestamp).toLocaleString()}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent snapshots */}
            {detail.recent_snapshots.length > 0 && (
              <div>
                <h4 className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-2">
                  Últimos Snapshots
                </h4>
                <div className="space-y-1">
                  {detail.recent_snapshots.map((snap) => (
                    <div
                      key={snap.snapshot_id}
                      className="flex items-center justify-between border border-white/5 bg-[#18181b]/10 p-2 rounded text-[10px] font-mono"
                    >
                      <span className="text-amber-400 font-bold">
                        {snap.symbol}
                      </span>
                      <span className="text-zinc-500">
                        {new Date(snap.timestamp).toLocaleString()}
                      </span>
                      <ChevronRight className="h-3 w-3 text-zinc-600" />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function computeHealthScore(
  errorRatePct: number,
  unresolvedErrors: number,
): number {
  let score = 100;
  score -= Math.min(errorRatePct * 5, 40);
  score -= Math.min(unresolvedErrors * 5, 30);
  return Math.max(0, Math.round(score));
}
