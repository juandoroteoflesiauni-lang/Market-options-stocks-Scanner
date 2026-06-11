"use client";

import * as React from "react";
import {
  Activity,
  DollarSign,
  TrendingUp,
  AlertTriangle,
  Database,
  RefreshCw,
  Sliders,
  ShieldAlert,
  Clock,
  ArrowRight,
  Percent,
} from "lucide-react";
import { useApiConsumption } from "@/hooks/use-api-consumption";
import { cn } from "@/lib/utils";

export function ApiConsumptionMonitor() {
  const { dashboard, rateLimiter, isLoading, error, refresh, resetStats } =
    useApiConsumption();
  const [selectedProvider, setSelectedProvider] = React.useState<string | null>(
    null,
  );
  const [showResetConfirm, setShowResetConfirm] = React.useState(false);

  const stats = dashboard;
  const elapsed = stats?.elapsed_hours ?? 0;
  const elapsedText =
    elapsed > 24
      ? `${(elapsed / 24).toFixed(1)} días`
      : `${elapsed.toFixed(1)} horas`;

  const handleReset = async () => {
    await resetStats();
    setShowResetConfirm(false);
  };

  const providerNames: string[] = stats?.providers
    ? Object.keys(stats.providers).sort()
    : [];

  // Set default selected provider once loaded
  if (providerNames.length > 0 && !selectedProvider) {
    setSelectedProvider(providerNames[0]);
  }

  const selectedStats =
    selectedProvider && stats?.providers
      ? stats.providers[selectedProvider]
      : null;

  return (
    <div className="min-h-screen bg-[#050506] text-[#f5f5f7] font-sans antialiased p-4 space-y-4">
      {/* Upper header */}
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-white/5 pb-4">
        <div>
          <span className="text-[9px] text-zinc-500 font-mono uppercase tracking-widest">
            Módulo de Monitoreo
          </span>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <Sliders className="h-5 w-5 text-amber-500" />
            API Consumer Monitor
          </h1>
          <p className="text-xs text-zinc-500 font-mono mt-0.5">
            Seguimiento de cuotas, estimación de costos en USD y análisis de
            latencias
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-zinc-400 border border-white/5 px-2.5 py-1 rounded bg-white/[0.02] flex items-center gap-1.5">
            <Clock className="h-3.5 w-3.5 text-zinc-500" />
            Periodo: <strong className="text-zinc-200">
              {elapsedText}
            </strong>{" "}
            acumulados
          </span>

          <button
            onClick={() => refresh()}
            disabled={isLoading}
            className="p-2 border border-white/5 rounded bg-white/[0.02] hover:bg-white/5 transition-all text-zinc-400 hover:text-white"
            title="Refrescar datos"
          >
            <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} />
          </button>

          {!showResetConfirm ? (
            <button
              onClick={() => setShowResetConfirm(true)}
              className="px-3 h-8 rounded border border-rose-500/20 bg-rose-500/10 hover:bg-rose-500 hover:text-void transition-all font-mono text-[10px] font-bold uppercase tracking-wider text-rose-400"
            >
              Resetear Métricas
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <button
                onClick={handleReset}
                className="px-3 h-8 rounded border border-red-500 bg-red-600 hover:bg-red-700 text-white transition-all font-mono text-[10px] font-bold uppercase tracking-wider"
              >
                Confirmar
              </button>
              <button
                onClick={() => setShowResetConfirm(false)}
                className="px-2 h-8 rounded border border-white/10 bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-all font-mono text-[10px]"
              >
                No
              </button>
            </div>
          )}
        </div>
      </header>

      {error && (
        <div className="border border-rose-500/20 bg-rose-500/10 text-rose-300 p-3 rounded-xl flex items-center gap-2.5 text-xs font-mono">
          <AlertTriangle className="h-4.5 w-4.5 text-rose-400 shrink-0" />
          <span>Error de conexión: {error}</span>
        </div>
      )}

      {/* Main summary cards */}
      <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
          <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
            <Activity className="h-3 w-3 text-zinc-500" /> Llamadas Totales
          </div>
          <div className="text-2xl font-mono font-bold text-white">
            {stats?.total_calls.toLocaleString() ?? "0"}
          </div>
          <div className="text-[8px] font-mono text-zinc-600 mt-1.5">
            Llamadas acumuladas
          </div>
        </div>

        <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
          <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
            <DollarSign className="h-3 w-3 text-zinc-500" /> Costo Estimado
          </div>
          <div className="text-2xl font-mono font-bold text-emerald-400">
            ${stats?.total_cost_usd.toFixed(4) ?? "0.0000"}
          </div>
          <div className="text-[8px] font-mono text-zinc-600 mt-1.5">
            Tarifas base acumuladas (USD)
          </div>
        </div>

        <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
          <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
            <TrendingUp className="h-3 w-3 text-zinc-500" /> Proyección Mensual
          </div>
          <div className="text-2xl font-mono font-bold text-blue-400">
            ${stats?.projected_monthly_cost_usd.toFixed(2) ?? "0.00"}
          </div>
          <div className="text-[8px] font-mono text-zinc-600 mt-1.5">
            Extrapolación a 730 horas
          </div>
        </div>

        <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
          <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
            <Percent className="h-3 w-3 text-zinc-500" /> Cache Hit Rate
          </div>
          <div className="text-2xl font-mono font-bold text-zinc-100">
            {stats?.overall_cache_hit_rate.toFixed(1) ?? "0.0"}%
          </div>
          <div className="text-[8px] font-mono text-zinc-600 mt-1.5 flex justify-between">
            <span>Hits: {stats?.total_cache_hits ?? 0}</span>
            <span>Misses: {stats?.total_cache_misses ?? 0}</span>
          </div>
        </div>

        <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
          <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3 text-zinc-500" /> Errores
          </div>
          <div
            className={cn(
              "text-2xl font-mono font-bold",
              (stats?.total_errors ?? 0) > 0
                ? "text-rose-400"
                : "text-zinc-400",
            )}
          >
            {stats?.total_errors ?? 0}
          </div>
          <div className="text-[8px] font-mono text-zinc-600 mt-1.5">
            Códigos HTTP 4xx, 5xx o timeout
          </div>
        </div>

        <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
          <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
            <ShieldAlert className="h-3 w-3 text-zinc-500" /> Rate Limited
          </div>
          <div
            className={cn(
              "text-2xl font-mono font-bold",
              (stats?.total_rate_limited ?? 0) > 0
                ? "text-amber-500"
                : "text-zinc-400",
            )}
          >
            {stats?.total_rate_limited ?? 0}
          </div>
          <div className="text-[8px] font-mono text-zinc-600 mt-1.5">
            Llamadas bloqueadas / HTTP 429
          </div>
        </div>
      </section>

      {/* Grid: Providers matrix & detail panel */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
        {/* Providers inventory matrix */}
        <section className="col-span-1 xl:col-span-8 bg-[#121215]/30 border border-white/5 rounded-2xl flex flex-col backdrop-blur-md overflow-hidden">
          <div className="p-4 border-b border-white/5 flex items-center justify-between">
            <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
              <Database className="h-3.5 w-3.5" />
              API Providers Matrix
            </h2>
            <span className="text-[9px] font-mono text-zinc-500">
              Proveedores registrados: {providerNames.length}
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#0c0c0e]/95 border-b border-white/10">
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase">
                    Proveedor
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-right">
                    Llamadas
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-center">
                    Hit Rate
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-center">
                    Latencia (p50/p99)
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-right">
                    Costo USD
                  </th>
                  <th className="font-mono text-[8.5px] text-zinc-500 font-semibold py-3 px-4 uppercase text-right">
                    Error Rate
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {providerNames.map((name) => {
                  const prov = stats?.providers[name];
                  if (!prov) return null;
                  const isSelected = selectedProvider === name;

                  return (
                    <tr
                      key={name}
                      onClick={() => setSelectedProvider(name)}
                      className={cn(
                        "group hover:bg-white/[0.02] cursor-pointer transition-colors",
                        isSelected ? "bg-white/[0.03]" : "",
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
                          {prov.total_calls.toLocaleString()}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span
                          className={cn(
                            "inline-flex items-center justify-center px-2 py-0.5 rounded-full text-[9px] font-mono border font-medium",
                            prov.cache_hit_rate > 50
                              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
                              : prov.cache_hit_rate > 20
                                ? "border-zinc-700/50 bg-zinc-800/30 text-zinc-400"
                                : "border-amber-500/10 bg-amber-500/5 text-amber-400",
                          )}
                        >
                          {prov.cache_hit_rate.toFixed(1)}%
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="font-mono text-xs text-zinc-400">
                          {prov.latency_p50_ms.toFixed(0)} /{" "}
                          {prov.latency_p99_ms.toFixed(0)} ms
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span className="font-mono text-xs text-emerald-400 font-bold">
                          ${prov.total_cost_usd.toFixed(5)}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span
                          className={cn(
                            "font-mono text-xs font-bold",
                            prov.error_rate > 5
                              ? "text-rose-400"
                              : prov.error_rate > 0
                                ? "text-amber-400"
                                : "text-zinc-500",
                          )}
                        >
                          {prov.error_rate.toFixed(1)}%
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        {/* Detailed Provider Panel */}
        <section className="col-span-1 xl:col-span-4 bg-[#121215]/30 border border-white/5 rounded-2xl flex flex-col backdrop-blur-md p-4 overflow-hidden">
          <div className="border-b border-white/5 pb-3 mb-3">
            <span className="text-[8px] text-zinc-500 font-mono uppercase tracking-wider">
              Detalle del Canal
            </span>
            <h3 className="text-sm font-bold uppercase text-white tracking-wide">
              {selectedProvider ?? "Ningún proveedor seleccionado"}
            </h3>
          </div>

          {selectedStats ? (
            <div className="space-y-4 flex-1 overflow-y-auto custom-scrollbar">
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-[#18181b]/35 border border-white/5 p-2 rounded-lg font-mono">
                  <div className="text-[8px] text-zinc-500 uppercase">
                    Mensual Proyectado
                  </div>
                  <div className="text-xs font-bold text-blue-400 mt-0.5">
                    ${selectedStats.projected_monthly_cost_usd.toFixed(2)}
                  </div>
                </div>
                <div className="bg-[#18181b]/35 border border-white/5 p-2 rounded-lg font-mono">
                  <div className="text-[8px] text-zinc-500 uppercase">
                    Latencia p50
                  </div>
                  <div className="text-xs font-bold text-zinc-200 mt-0.5">
                    {selectedStats.latency_p50_ms.toFixed(0)} ms
                  </div>
                </div>
              </div>

              {/* Endpoints breakdown */}
              <div>
                <h4 className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-1.5">
                  Top Endpoints (Llamadas)
                </h4>
                <div className="space-y-1">
                  {selectedStats.top_endpoints?.length > 0 ? (
                    selectedStats.top_endpoints.map(([ep, count]) => (
                      <div
                        key={ep}
                        className="flex items-center justify-between border border-white/5 bg-[#18181b]/10 p-2 rounded text-[10px] font-mono hover:border-white/10"
                      >
                        <span
                          className="text-zinc-400 truncate max-w-[200px]"
                          title={ep}
                        >
                          {ep}
                        </span>
                        <span className="text-zinc-200 font-bold shrink-0">
                          {count}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="text-[10px] text-zinc-600 font-mono p-2 border border-dashed border-white/5 rounded">
                      Sin datos de endpoints.
                    </div>
                  )}
                </div>
              </div>

              {/* API keys breakdown */}
              <div>
                <h4 className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-1.5">
                  Uso por Clave (Key Label)
                </h4>
                <div className="space-y-1">
                  {selectedStats.top_api_keys?.length > 0 ? (
                    selectedStats.top_api_keys.map(([key, count]) => (
                      <div
                        key={key}
                        className="flex items-center justify-between border border-white/5 bg-[#18181b]/10 p-2 rounded text-[10px] font-mono"
                      >
                        <span className="text-zinc-400 truncate">
                          Key: <strong className="text-zinc-300">{key}</strong>
                        </span>
                        <span className="text-zinc-200 font-bold shrink-0">
                          {count}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="text-[10px] text-zinc-600 font-mono p-2 border border-dashed border-white/5 rounded">
                      Sin datos de keys.
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 text-zinc-600 font-mono text-[10px]">
              Selecciona un proveedor de la tabla para ver su telemetría.
            </div>
          )}
        </section>
      </div>

      {/* Rate Limiter State */}
      <section className="bg-[#121215]/30 border border-white/5 rounded-2xl flex flex-col backdrop-blur-md overflow-hidden">
        <div className="p-4 border-b border-white/5 flex items-center justify-between">
          <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
            <Sliders className="h-3.5 w-3.5 text-zinc-500" />
            Rate Limiter Buckets (Token Buckets)
          </h2>
        </div>

        <div className="p-4">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            {rateLimiter?.buckets &&
            Object.keys(rateLimiter.buckets).length > 0 ? (
              Object.entries(rateLimiter.buckets).map(([name, bucket]) => {
                const occupancy =
                  bucket.burst > 0
                    ? Math.min(100, (bucket.tokens / bucket.burst) * 100)
                    : 0;

                return (
                  <div
                    key={name}
                    className="border border-white/5 bg-[#18181b]/20 p-3 rounded-xl flex flex-col justify-between hover:border-white/10 transition-all font-mono"
                  >
                    <div>
                      <div className="text-[9px] text-zinc-500 uppercase">
                        {name}
                      </div>
                      <div className="text-base font-bold text-zinc-100 mt-1 flex justify-between items-baseline">
                        <span>{bucket.tokens.toFixed(1)} tokens</span>
                        <span className="text-[10px] text-zinc-500 font-normal">
                          Max: {bucket.burst}
                        </span>
                      </div>
                    </div>

                    <div className="mt-3">
                      <div className="flex justify-between items-center text-[8px] text-zinc-600 mb-1">
                        <span>Ocupación</span>
                        <span>{occupancy.toFixed(0)}%</span>
                      </div>
                      <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden border border-white/5">
                        <div
                          className={cn(
                            "h-full rounded-full transition-all duration-300",
                            occupancy > 70
                              ? "bg-emerald-500"
                              : occupancy > 30
                                ? "bg-amber-500"
                                : "bg-rose-500",
                          )}
                          style={{ width: `${occupancy}%` }}
                        />
                      </div>
                    </div>

                    <div className="flex justify-between items-center mt-2.5 text-[8px] text-zinc-600 border-t border-white/5 pt-2">
                      <span>Rate: {bucket.rate}/s</span>
                      {bucket.last_request > 0 && (
                        <span>
                          Refresco:{" "}
                          {new Date(bucket.last_request * 1000)
                            .toISOString()
                            .slice(14, 21)}
                          s
                        </span>
                      )}
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="col-span-full border border-dashed border-white/5 rounded-xl p-6 text-center text-[10px] font-mono text-zinc-600">
                Ningún bucket de rate limiter registrado o activo.
              </div>
            )}
          </div>
        </div>
      </section>

      <style
        dangerouslySetInnerHTML={{
          __html: `
        .custom-scrollbar::-webkit-scrollbar {
          width: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: rgba(0,0,0,0.2);
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(255,255,255,0.06);
          border-radius: 10px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(255,255,255,0.15);
        }
      `,
        }}
      />
    </div>
  );
}
