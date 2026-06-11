"use client";

import * as React from "react";
import {
  Activity,
  DollarSign,
  TrendingUp,
  AlertTriangle,
  Clock,
  ShieldAlert,
  Percent,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  ApiConsumptionByModule,
  CostProjection,
  RateLimitData,
} from "@/hooks/use-audit-complex";

interface Props {
  apiConsumption: ApiConsumptionByModule | null;
  costProjections: CostProjection | null;
  rateLimits: RateLimitData | null;
  isLoading: boolean;
}

export function ApiAuditTab({
  apiConsumption,
  costProjections,
  rateLimits,
  isLoading,
}: Props) {
  const [selectedModule, setSelectedModule] = React.useState<string | null>(
    null,
  );

  const modules = React.useMemo(() => {
    if (!apiConsumption) return [];
    return Object.keys(apiConsumption.modules).sort();
  }, [apiConsumption]);

  React.useEffect(() => {
    if (modules.length > 0 && !selectedModule) {
      setSelectedModule(modules[0]);
    }
  }, [modules, selectedModule]);

  const selectedStats =
    selectedModule && apiConsumption?.modules
      ? apiConsumption.modules[selectedModule]
      : null;

  const selectedProviders =
    selectedModule && apiConsumption?.provider_breakdown
      ? apiConsumption.provider_breakdown[selectedModule]
      : null;

  const totalCost = apiConsumption
    ? Object.values(apiConsumption.modules).reduce(
        (s, m) => s + m.total_cost_usd,
        0,
      )
    : 0;
  const totalCalls = apiConsumption
    ? Object.values(apiConsumption.modules).reduce(
        (s, m) => s + m.total_calls,
        0,
      )
    : 0;
  const totalErrors = apiConsumption
    ? Object.values(apiConsumption.modules).reduce(
        (s, m) => s + m.error_calls,
        0,
      )
    : 0;
  const totalRateLimited = rateLimits?.total_rate_limited ?? 0;

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          icon={<DollarSign className="h-3 w-3 text-zinc-500" />}
          label="Costo Total"
          value={`$${totalCost.toFixed(4)}`}
          color="text-emerald-400"
          sub="USD acumulado"
        />
        <MetricCard
          icon={<Activity className="h-3 w-3 text-zinc-500" />}
          label="Llamadas API"
          value={totalCalls.toLocaleString()}
          color="text-white"
          sub="acumuladas"
        />
        <MetricCard
          icon={<AlertTriangle className="h-3 w-3 text-zinc-500" />}
          label="Errores"
          value={String(totalErrors)}
          color={totalErrors > 0 ? "text-rose-400" : "text-zinc-400"}
          sub="4xx/5xx/timeout"
        />
        <MetricCard
          icon={<ShieldAlert className="h-3 w-3 text-zinc-500" />}
          label="Rate Limited"
          value={String(totalRateLimited)}
          color={totalRateLimited > 0 ? "text-amber-500" : "text-zinc-400"}
          sub="HTTP 429"
        />
      </section>

      {/* Cost projections */}
      {costProjections && (
        <section className="bg-[#121215]/30 border border-white/5 rounded-2xl p-4 backdrop-blur-md">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
              <TrendingUp className="h-3.5 w-3.5" />
              Proyección de Costos Mensual
            </h3>
            <span className="font-mono text-sm font-bold text-blue-400">
              ${costProjections.total_projected_monthly_usd.toFixed(2)}/mes
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {Object.entries(costProjections.modules).map(([mod, data]) => (
              <div
                key={mod}
                className="border border-white/5 bg-[#18181b]/20 p-3 rounded-lg font-mono"
              >
                <div className="text-[9px] text-zinc-500 uppercase">{mod}</div>
                <div className="flex justify-between items-baseline mt-1">
                  <span className="text-xs font-bold text-blue-400">
                    ${data.projected_monthly_usd.toFixed(2)}/mes
                  </span>
                  <span className="text-[9px] text-zinc-600">
                    {data.total_calls} calls / {data.hours_tracked.toFixed(1)}h
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Module matrix + detail */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
        <section className="col-span-1 xl:col-span-8 bg-[#121215]/30 border border-white/5 rounded-2xl flex flex-col backdrop-blur-md overflow-hidden">
          <div className="p-4 border-b border-white/5 flex items-center justify-between">
            <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
              <Activity className="h-3.5 w-3.5" />
              Consumo API por Módulo
            </h2>
            <span className="text-[9px] font-mono text-zinc-500">
              {modules.length} módulos
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#0c0c0e]/95 border-b border-white/10">
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase">
                    Módulo
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-right">
                    Llamadas
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-center">
                    Cache Hit
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-center">
                    Latencia
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-right">
                    Costo
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-right">
                    Error Rate
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {modules.map((name) => {
                  const mod = apiConsumption?.modules[name];
                  if (!mod) return null;
                  const isSelected = selectedModule === name;
                  return (
                    <tr
                      key={name}
                      onClick={() => setSelectedModule(name)}
                      className={cn(
                        "group hover:bg-white/[0.02] cursor-pointer transition-colors",
                        isSelected && "bg-white/[0.03]",
                      )}
                    >
                      <td className="py-3 px-4">
                        <span
                          className={cn(
                            "font-mono text-xs font-bold uppercase",
                            isSelected ? "text-amber-400" : "text-zinc-300",
                          )}
                        >
                          {name}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span className="font-mono text-xs text-zinc-300">
                          {mod.total_calls.toLocaleString()}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span
                          className={cn(
                            "inline-flex items-center justify-center px-2 py-0.5 rounded-full text-[9px] font-mono border font-medium",
                            mod.cache_hit_rate_pct > 50
                              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
                              : mod.cache_hit_rate_pct > 20
                                ? "border-zinc-700/50 bg-zinc-800/30 text-zinc-400"
                                : "border-amber-500/10 bg-amber-500/5 text-amber-400",
                          )}
                        >
                          {mod.cache_hit_rate_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="font-mono text-xs text-zinc-400">
                          {mod.avg_duration_ms.toFixed(0)} ms
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span className="font-mono text-xs text-emerald-400 font-bold">
                          ${mod.total_cost_usd.toFixed(5)}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span
                          className={cn(
                            "font-mono text-xs font-bold",
                            mod.error_rate_pct > 5
                              ? "text-rose-400"
                              : mod.error_rate_pct > 0
                                ? "text-amber-400"
                                : "text-zinc-500",
                          )}
                        >
                          {mod.error_rate_pct.toFixed(1)}%
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        {/* Detail panel */}
        <section className="col-span-1 xl:col-span-4 bg-[#121215]/30 border border-white/5 rounded-2xl flex flex-col backdrop-blur-md p-4 overflow-hidden">
          <div className="border-b border-white/5 pb-3 mb-3">
            <span className="text-[8px] text-zinc-500 font-mono uppercase tracking-wider">
              Detalle del Módulo
            </span>
            <h3 className="text-sm font-bold uppercase text-white tracking-wide">
              {selectedModule ?? "Seleccionar módulo"}
            </h3>
          </div>

          {selectedStats ? (
            <div className="space-y-4 flex-1 overflow-y-auto custom-scrollbar">
              <div className="grid grid-cols-2 gap-2">
                <MiniStat
                  label="Proyección Mensual"
                  value={`$${((selectedStats.total_cost_usd * 730) / Math.max(1, selectedStats.total_calls)).toFixed(2)}`}
                />
                <MiniStat
                  label="Latencia Avg"
                  value={`${selectedStats.avg_duration_ms.toFixed(0)} ms`}
                />
                <MiniStat
                  label="Cache Hits"
                  value={`${selectedStats.cache_hit_rate_pct.toFixed(1)}%`}
                />
                <MiniStat
                  label="Rate Limited"
                  value={String(selectedStats.rate_limited)}
                />
              </div>

              {selectedProviders && (
                <div>
                  <h4 className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-1.5">
                    Providers
                  </h4>
                  <div className="space-y-1">
                    {Object.entries(selectedProviders).map(([prov, data]) => (
                      <div
                        key={prov}
                        className="flex items-center justify-between border border-white/5 bg-[#18181b]/10 p-2 rounded text-[10px] font-mono"
                      >
                        <span className="text-zinc-400">{prov}</span>
                        <span className="text-zinc-200 font-bold">
                          {data.calls} calls · ${data.cost_usd.toFixed(5)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-600 font-mono text-[10px]">
              Selecciona un módulo.
            </div>
          )}
        </section>
      </div>

      {/* Rate Limiter */}
      {rateLimits && rateLimits.total_rate_limited > 0 && (
        <section className="bg-[#121215]/30 border border-white/5 rounded-2xl p-4 backdrop-blur-md">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2 mb-3">
            <ShieldAlert className="h-3.5 w-3.5 text-amber-500" />
            Rate Limits Recientes
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
            {Object.entries(rateLimits.by_module).map(([mod, count]) => (
              <div
                key={mod}
                className="border border-amber-500/10 bg-amber-500/5 p-2 rounded font-mono"
              >
                <div className="text-[9px] text-zinc-500 uppercase">{mod}</div>
                <div className="text-sm font-bold text-amber-400">{count}</div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  color,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color: string;
  sub: string;
}) {
  return (
    <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
      <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
        {icon} {label}
      </div>
      <div className={cn("text-2xl font-mono font-bold", color)}>{value}</div>
      <div className="text-[8px] font-mono text-zinc-600 mt-1.5">{sub}</div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#18181b]/35 border border-white/5 p-2 rounded-lg font-mono">
      <div className="text-[8px] text-zinc-500 uppercase">{label}</div>
      <div className="text-xs font-bold text-zinc-200 mt-0.5">{value}</div>
    </div>
  );
}
