"use client";

import * as React from "react";
import { Bug, CheckCircle, ChevronRight, X, Link2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AuditError, ErrorStats } from "@/hooks/use-audit-complex";

interface Props {
  errorStats: ErrorStats | null;
  fetchErrors: (params?: {
    module?: string;
    severity?: string;
    resolved?: boolean;
    limit?: number;
  }) => Promise<{ errors: AuditError[]; total: number }>;
  resolveError: (
    errorId: string,
    resolvedBy: string,
    notes: string,
  ) => Promise<void>;
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "text-red-500 border-red-500/20 bg-red-500/10",
  error: "text-rose-400 border-rose-500/20 bg-rose-500/10",
  warning: "text-amber-400 border-amber-500/20 bg-amber-500/10",
};

export function ErrorDashboard({
  errorStats,
  fetchErrors,
  resolveError,
}: Props) {
  const [errors, setErrors] = React.useState<AuditError[]>([]);
  const [filterModule, setFilterModule] = React.useState("");
  const [filterSeverity, setFilterSeverity] = React.useState("");
  const [filterResolved, setFilterResolved] = React.useState<string>("");
  const [selected, setSelected] = React.useState<AuditError | null>(null);
  const [resolveNotes, setResolveNotes] = React.useState("");
  const [isResolving, setIsResolving] = React.useState(false);
  const [isLoading, setIsLoading] = React.useState(false);

  const load = React.useCallback(async () => {
    setIsLoading(true);
    try {
      const result = await fetchErrors({
        module: filterModule || undefined,
        severity: filterSeverity || undefined,
        resolved: filterResolved === "" ? undefined : filterResolved === "true",
        limit: 100,
      });
      setErrors(result.errors);
    } catch {
      // silent
    } finally {
      setIsLoading(false);
    }
  }, [fetchErrors, filterModule, filterSeverity, filterResolved]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const handleResolve = async (errorId: string) => {
    setIsResolving(true);
    try {
      await resolveError(errorId, "dashboard-user", resolveNotes);
      setResolveNotes("");
      setSelected(null);
      await load();
    } catch {
      // silent
    } finally {
      setIsResolving(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Stats cards */}
      {errorStats && (
        <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            label="Total Errores"
            value={String(errorStats.total_errors)}
            color="text-zinc-200"
          />
          <StatCard
            label="Resueltos"
            value={String(errorStats.total_resolved)}
            color="text-emerald-400"
            icon={<CheckCircle className="h-3 w-3" />}
          />
          <StatCard
            label="Sin Resolver"
            value={String(errorStats.total_unresolved)}
            color={
              errorStats.total_unresolved > 0
                ? "text-rose-400"
                : "text-zinc-400"
            }
            icon={<Bug className="h-3 w-3" />}
          />
          <StatCard
            label="Tasa Resolución"
            value={
              errorStats.total_errors > 0
                ? `${((errorStats.total_resolved / errorStats.total_errors) * 100).toFixed(0)}%`
                : "N/A"
            }
            color="text-blue-400"
          />
        </section>
      )}

      {/* Per-module stats */}
      {errorStats && Object.keys(errorStats.by_module).length > 0 && (
        <section className="bg-[#121215]/30 border border-white/5 rounded-2xl p-4 backdrop-blur-md">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">
            Errores por Módulo
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {Object.entries(errorStats.by_module).map(([mod, stats]) => (
              <div
                key={mod}
                className="border border-white/5 bg-[#18181b]/20 p-3 rounded-lg font-mono"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[9px] text-zinc-500 uppercase">
                    {mod}
                  </span>
                  <span
                    className={cn(
                      "text-xs font-bold",
                      stats.unresolved > 0
                        ? "text-rose-400"
                        : "text-emerald-400",
                    )}
                  >
                    {stats.unresolved} abiertos
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-1 text-[8px]">
                  <div>
                    <span className="text-zinc-600">Crit:</span>{" "}
                    <span className="text-red-500 font-bold">
                      {stats.critical}
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-600">Err:</span>{" "}
                    <span className="text-rose-400 font-bold">
                      {stats.errors}
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-600">Warn:</span>{" "}
                    <span className="text-amber-400 font-bold">
                      {stats.warnings}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3">
        <select
          value={filterSeverity}
          onChange={(e) => setFilterSeverity(e.target.value)}
          className="h-8 px-2 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 focus:outline-none"
        >
          <option value="">Todas severidades</option>
          <option value="critical">CRITICAL</option>
          <option value="error">ERROR</option>
          <option value="warning">WARNING</option>
        </select>
        <select
          value={filterResolved}
          onChange={(e) => setFilterResolved(e.target.value)}
          className="h-8 px-2 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 focus:outline-none"
        >
          <option value="">Todos</option>
          <option value="false">Sin resolver</option>
          <option value="true">Resueltos</option>
        </select>
        <input
          value={filterModule}
          onChange={(e) => setFilterModule(e.target.value)}
          placeholder="Módulo..."
          className="h-8 px-3 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-white/10 w-32"
        />
        <button
          onClick={() => void load()}
          disabled={isLoading}
          className="h-8 px-3 bg-[#1E2D47] border border-[rgba(0,195,255,0.20)] rounded text-[10px] font-mono text-zinc-300 hover:text-white transition-all"
        >
          Filtrar
        </button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
        {/* Error list */}
        <section
          className={cn(
            "col-span-1 bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden flex flex-col",
            selected ? "xl:col-span-5" : "xl:col-span-12",
          )}
        >
          <div className="p-4 border-b border-white/5 flex items-center justify-between">
            <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
              <Bug className="h-3.5 w-3.5" />
              Registro de Errores ({errors.length})
            </h3>
          </div>
          <div className="overflow-y-auto max-h-[500px] custom-scrollbar divide-y divide-white/5">
            {errors.length === 0 ? (
              <div className="p-6 text-center text-[10px] font-mono text-zinc-600">
                Sin errores registrados.
              </div>
            ) : (
              errors.map((err) => (
                <div
                  key={err.error_id}
                  onClick={() => setSelected(err)}
                  className={cn(
                    "px-4 py-3 cursor-pointer transition-colors hover:bg-white/[0.02]",
                    selected?.error_id === err.error_id && "bg-white/[0.03]",
                    err.resolved && "opacity-50",
                  )}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className={cn(
                        "text-[8px] font-mono font-bold uppercase px-1.5 py-0.5 rounded border",
                        SEVERITY_COLORS[err.severity],
                      )}
                    >
                      {err.severity}
                    </span>
                    <span className="text-[9px] font-mono text-amber-400 uppercase">
                      {err.module}
                    </span>
                    {err.resolved && (
                      <span className="text-[8px] font-mono text-emerald-400 flex items-center gap-0.5">
                        <CheckCircle className="h-3 w-3" /> resuelto
                      </span>
                    )}
                    <span className="text-[9px] font-mono text-zinc-600 ml-auto">
                      {new Date(err.timestamp).toLocaleString()}
                    </span>
                  </div>
                  <p className="text-[10px] font-mono text-zinc-300 truncate">
                    {err.message}
                  </p>
                </div>
              ))
            )}
          </div>
        </section>

        {/* Error detail */}
        {selected && (
          <section className="col-span-1 xl:col-span-7 bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md p-4 overflow-hidden">
            <div className="flex items-center justify-between mb-4">
              <div>
                <span className="text-[8px] text-zinc-500 font-mono uppercase tracking-wider">
                  Detalle del Error
                </span>
                <h3 className="text-sm font-bold text-rose-400 font-mono">
                  {selected.error_type}
                </h3>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="p-1 text-zinc-500 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3 overflow-y-auto max-h-[450px] custom-scrollbar">
              <div className="border border-white/5 rounded-lg p-3">
                <div className="text-[9px] text-zinc-500 font-mono uppercase mb-1">
                  Mensaje
                </div>
                <p className="text-xs font-mono text-zinc-200">
                  {selected.message}
                </p>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <InfoBlock label="Módulo" value={selected.module} />
                <InfoBlock label="Severidad" value={selected.severity} />
                <InfoBlock
                  label="Correlation ID"
                  value={selected.correlation_id || "N/A"}
                />
              </div>

              {selected.stack_trace && (
                <div className="border border-white/5 rounded-lg overflow-hidden">
                  <div className="bg-[#0c0c0e]/50 px-3 py-1.5 border-b border-white/5">
                    <span className="text-[9px] font-mono font-bold text-zinc-500 uppercase">
                      Stack Trace
                    </span>
                  </div>
                  <pre className="p-3 text-[10px] font-mono text-rose-300 overflow-x-auto whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto">
                    {selected.stack_trace}
                  </pre>
                </div>
              )}

              {Object.keys(selected.context).length > 0 && (
                <div className="border border-white/5 rounded-lg overflow-hidden">
                  <div className="bg-[#0c0c0e]/50 px-3 py-1.5 border-b border-white/5">
                    <span className="text-[9px] font-mono font-bold text-zinc-500 uppercase">
                      Contexto
                    </span>
                  </div>
                  <pre className="p-3 text-[10px] font-mono text-zinc-300 overflow-x-auto whitespace-pre-wrap break-all">
                    {JSON.stringify(selected.context, null, 2)}
                  </pre>
                </div>
              )}

              {/* Resolve action */}
              {!selected.resolved && (
                <div className="border border-emerald-500/20 bg-emerald-500/5 rounded-lg p-3">
                  <div className="text-[9px] text-emerald-400 font-mono uppercase mb-2 font-bold">
                    Marcar como Resuelto
                  </div>
                  <input
                    value={resolveNotes}
                    onChange={(e) => setResolveNotes(e.target.value)}
                    placeholder="Notas de resolución..."
                    className="w-full h-7 px-2 bg-[#121215]/50 border border-white/5 rounded text-[10px] font-mono text-zinc-300 placeholder:text-zinc-600 focus:outline-none mb-2"
                  />
                  <button
                    onClick={() => void handleResolve(selected.error_id)}
                    disabled={isResolving}
                    className="h-7 px-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[10px] font-mono font-bold uppercase transition-all"
                  >
                    {isResolving ? "Resolviendo..." : "Resolver"}
                  </button>
                </div>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
  icon,
}: {
  label: string;
  value: string;
  color: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="bg-[#121215]/30 border border-white/5 p-4 rounded-2xl backdrop-blur-md">
      <div className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase mb-1 flex items-center gap-1">
        {icon} {label}
      </div>
      <div className={cn("text-2xl font-mono font-bold", color)}>{value}</div>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-white/5 rounded-lg p-2 font-mono">
      <div className="text-[8px] text-zinc-500 uppercase">{label}</div>
      <div className="text-[10px] text-zinc-200 font-bold truncate">
        {value}
      </div>
    </div>
  );
}
