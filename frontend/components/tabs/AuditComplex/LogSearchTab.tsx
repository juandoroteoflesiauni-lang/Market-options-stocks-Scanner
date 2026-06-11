"use client";

import * as React from "react";
import {
  Search,
  Link2,
  AlertCircle,
  Info,
  AlertTriangle,
  ChevronRight,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { AuditLogEntry, LogStats } from "@/hooks/use-audit-complex";

interface Props {
  logStats: LogStats | null;
  fetchLogs: (params?: {
    query?: string;
    module?: string;
    level?: string;
    correlation_id?: string;
    tag?: string;
    limit?: number;
  }) => Promise<{ logs: AuditLogEntry[]; total_matching: number }>;
  fetchLogTrace: (correlationId: string) => Promise<AuditLogEntry[]>;
}

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "text-zinc-500",
  INFO: "text-blue-400",
  WARNING: "text-amber-400",
  ERROR: "text-rose-400",
  CRITICAL: "text-red-500",
};

const LEVEL_BG: Record<string, string> = {
  DEBUG: "bg-zinc-500/10 border-zinc-500/20",
  INFO: "bg-blue-500/10 border-blue-500/20",
  WARNING: "bg-amber-500/10 border-amber-500/20",
  ERROR: "bg-rose-500/10 border-rose-500/20",
  CRITICAL: "bg-red-500/10 border-red-500/20",
};

export function LogSearchTab({ logStats, fetchLogs, fetchLogTrace }: Props) {
  const [logs, setLogs] = React.useState<AuditLogEntry[]>([]);
  const [query, setQuery] = React.useState("");
  const [filterModule, setFilterModule] = React.useState("");
  const [filterLevel, setFilterLevel] = React.useState("");
  const [traceId, setTraceId] = React.useState<string | null>(null);
  const [traceLogs, setTraceLogs] = React.useState<AuditLogEntry[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);

  const search = React.useCallback(async () => {
    setIsLoading(true);
    try {
      const result = await fetchLogs({
        query: query || undefined,
        module: filterModule || undefined,
        level: filterLevel || undefined,
        limit: 200,
      });
      setLogs(result.logs);
    } catch {
      // silent
    } finally {
      setIsLoading(false);
    }
  }, [fetchLogs, query, filterModule, filterLevel]);

  const loadTrace = React.useCallback(
    async (correlationId: string) => {
      setTraceId(correlationId);
      try {
        const result = await fetchLogTrace(correlationId);
        setTraceLogs(result);
      } catch {
        setTraceLogs([]);
      }
    },
    [fetchLogTrace],
  );

  React.useEffect(() => {
    void search();
  }, [search]);

  return (
    <div className="space-y-4">
      {/* Stats overview */}
      {logStats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          {Object.entries(logStats.by_level).map(([level, count]) => (
            <div
              key={level}
              className={cn(
                "border p-2 rounded-lg font-mono",
                LEVEL_BG[level] || "bg-zinc-500/10 border-zinc-500/20",
              )}
            >
              <div className={cn("text-[9px] uppercase", LEVEL_COLORS[level])}>
                {level}
              </div>
              <div className="text-sm font-bold text-zinc-200">
                {count.toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Search bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void search()}
            placeholder="Buscar en logs..."
            className="w-full h-8 pl-9 pr-3 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-white/10"
          />
        </div>
        <select
          value={filterLevel}
          onChange={(e) => setFilterLevel(e.target.value)}
          className="h-8 px-2 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 focus:outline-none"
        >
          <option value="">Todos niveles</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
        <input
          value={filterModule}
          onChange={(e) => setFilterModule(e.target.value)}
          placeholder="Módulo..."
          className="h-8 px-3 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-white/10 w-32"
        />
        <button
          onClick={() => void search()}
          disabled={isLoading}
          className="h-8 px-3 bg-[#1E2D47] border border-[rgba(0,195,255,0.20)] rounded text-[10px] font-mono text-zinc-300 hover:text-white transition-all"
        >
          Buscar
        </button>
      </div>

      {/* Logs list */}
      <section className="bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden">
        <div className="p-4 border-b border-white/5 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
            Logs ({logs.length})
          </h3>
        </div>
        <div className="overflow-y-auto max-h-[500px] custom-scrollbar divide-y divide-white/5">
          {logs.length === 0 ? (
            <div className="p-6 text-center text-[10px] font-mono text-zinc-600">
              Sin logs encontrados.
            </div>
          ) : (
            logs.map((log) => (
              <div
                key={log.log_id}
                className="px-4 py-2.5 hover:bg-white/[0.02] transition-colors"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={cn(
                      "text-[9px] font-mono font-bold uppercase px-1.5 py-0.5 rounded border",
                      LEVEL_COLORS[log.level],
                      LEVEL_BG[log.level],
                    )}
                  >
                    {log.level}
                  </span>
                  <span className="text-[9px] font-mono text-amber-400 uppercase">
                    {log.module}
                  </span>
                  <span className="text-[9px] font-mono text-zinc-600">
                    {log.logger_name}
                  </span>
                  <span className="text-[9px] font-mono text-zinc-600 ml-auto">
                    {new Date(log.timestamp).toLocaleString()}
                  </span>
                  {log.correlation_id && (
                    <button
                      onClick={() => void loadTrace(log.correlation_id)}
                      className="flex items-center gap-0.5 text-[9px] font-mono text-blue-400 hover:text-blue-300"
                      title="Ver trace completo"
                    >
                      <Link2 className="h-3 w-3" />
                      trace
                    </button>
                  )}
                </div>
                <p className="text-[11px] font-mono text-zinc-300 break-all">
                  {log.message}
                </p>
                {log.tags.length > 0 && (
                  <div className="flex gap-1 mt-1">
                    {log.tags.map((tag) => (
                      <span
                        key={tag}
                        className="text-[8px] font-mono text-zinc-500 bg-white/5 px-1.5 py-0.5 rounded"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </section>

      {/* Trace modal */}
      {traceId && (
        <section className="bg-[#121215]/30 border border-blue-500/20 rounded-2xl backdrop-blur-md overflow-hidden">
          <div className="p-4 border-b border-blue-500/10 flex items-center justify-between">
            <h3 className="text-xs font-semibold text-blue-400 uppercase tracking-wider flex items-center gap-2">
              <Link2 className="h-3.5 w-3.5" />
              Trace: {traceId}
            </h3>
            <button
              onClick={() => {
                setTraceId(null);
                setTraceLogs([]);
              }}
              className="p-1 text-zinc-500 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="overflow-y-auto max-h-[400px] custom-scrollbar divide-y divide-white/5">
            {traceLogs.map((log) => (
              <div key={log.log_id} className="px-4 py-2">
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className={cn(
                      "text-[8px] font-mono font-bold uppercase px-1 py-0.5 rounded border",
                      LEVEL_COLORS[log.level],
                      LEVEL_BG[log.level],
                    )}
                  >
                    {log.level}
                  </span>
                  <span className="text-[8px] font-mono text-amber-400">
                    {log.module}
                  </span>
                  <span className="text-[8px] font-mono text-zinc-600 ml-auto">
                    {new Date(log.timestamp).toLocaleTimeString()}
                  </span>
                </div>
                <p className="text-[10px] font-mono text-zinc-300">
                  {log.message}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
