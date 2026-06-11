"use client";

import * as React from "react";
import { Database, ChevronRight, Eye, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ProcessSnapshot } from "@/hooks/use-audit-complex";

interface Props {
  fetchSnapshots: (params?: {
    module?: string;
    symbol?: string;
    limit?: number;
  }) => Promise<{ snapshots: ProcessSnapshot[]; total: number }>;
}

export function DatabaseAuditTab({ fetchSnapshots }: Props) {
  const [snapshots, setSnapshots] = React.useState<ProcessSnapshot[]>([]);
  const [total, setTotal] = React.useState(0);
  const [filterModule, setFilterModule] = React.useState("");
  const [filterSymbol, setFilterSymbol] = React.useState("");
  const [selected, setSelected] = React.useState<ProcessSnapshot | null>(null);
  const [isLoading, setIsLoading] = React.useState(false);

  const load = React.useCallback(async () => {
    setIsLoading(true);
    try {
      const result = await fetchSnapshots({
        module: filterModule || undefined,
        symbol: filterSymbol || undefined,
        limit: 100,
      });
      setSnapshots(result.snapshots);
      setTotal(result.total);
    } catch {
      // silent
    } finally {
      setIsLoading(false);
    }
  }, [fetchSnapshots, filterModule, filterSymbol]);

  React.useEffect(() => {
    const id = setTimeout(() => void load(), 0);
    return () => clearTimeout(id);
  }, [load]);

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-3">
        <input
          value={filterModule}
          onChange={(e) => setFilterModule(e.target.value)}
          placeholder="Filtrar módulo..."
          className="h-8 px-3 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-white/10 w-40"
        />
        <input
          value={filterSymbol}
          onChange={(e) => setFilterSymbol(e.target.value)}
          placeholder="Filtrar símbolo (ej: MSFT-USDT)..."
          className="h-8 px-3 bg-[#121215]/50 border border-white/5 rounded text-xs font-mono text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-white/10 w-60"
        />
        <button
          onClick={() => void load()}
          disabled={isLoading}
          className="h-8 px-3 bg-[#1E2D47] border border-[rgba(0,195,255,0.20)] rounded text-[10px] font-mono text-zinc-300 hover:text-white transition-all"
        >
          Buscar
        </button>
        <span className="text-[9px] font-mono text-zinc-500">
          {total} snapshots encontrados
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
        {/* List */}
        <section
          className={cn(
            "col-span-1 bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden flex flex-col",
            selected ? "xl:col-span-5" : "xl:col-span-12",
          )}
        >
          <div className="p-4 border-b border-white/5">
            <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
              <Database className="h-3.5 w-3.5" />
              Process Snapshots
            </h3>
          </div>
          <div className="overflow-y-auto max-h-[500px] custom-scrollbar">
            {snapshots.length === 0 ? (
              <div className="p-6 text-center text-[10px] font-mono text-zinc-600">
                Sin snapshots de proceso registrados.
              </div>
            ) : (
              snapshots.map((snap) => (
                <div
                  key={snap.snapshot_id}
                  onClick={() => setSelected(snap)}
                  className={cn(
                    "flex items-center justify-between px-4 py-3 border-b border-white/5 cursor-pointer transition-colors hover:bg-white/[0.02]",
                    selected?.snapshot_id === snap.snapshot_id &&
                      "bg-white/[0.03]",
                  )}
                >
                  <div>
                    <span className="font-mono text-xs font-bold text-amber-400 uppercase">
                      {snap.symbol}
                    </span>
                    <span className="font-mono text-[9px] text-zinc-500 ml-2">
                      {snap.module}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[9px] text-zinc-600">
                      {new Date(snap.timestamp).toLocaleString()}
                    </span>
                    <ChevronRight className="h-3 w-3 text-zinc-600" />
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        {/* Detail */}
        {selected && (
          <section className="col-span-1 xl:col-span-7 bg-[#121215]/30 border border-white/5 rounded-2xl backdrop-blur-md p-4 overflow-hidden">
            <div className="flex items-center justify-between mb-4">
              <div>
                <span className="text-[8px] text-zinc-500 font-mono uppercase tracking-wider">
                  Snapshot Detallado
                </span>
                <h3 className="text-sm font-bold text-white font-mono flex items-center gap-2">
                  <span className="text-amber-400">{selected.symbol}</span>
                  <span className="text-zinc-500 text-xs">
                    {selected.module}
                  </span>
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
              <JsonBlock title="Indicadores" data={selected.indicators} />
              <JsonBlock title="Market Data" data={selected.market_data} />
              <JsonBlock title="Señales" data={selected.signals} />
              <JsonBlock title="Decisiones" data={selected.decisions} />
              <JsonBlock title="Risk Metrics" data={selected.risk_metrics} />
              <JsonBlock title="Orderbook" data={selected.orderbook} />
              <JsonBlock title="Engine State" data={selected.engine_state} />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function JsonBlock({
  title,
  data,
}: {
  title: string;
  data: Record<string, unknown>;
}) {
  const isEmpty = !data || Object.keys(data).length === 0;
  if (isEmpty) return null;

  return (
    <div className="border border-white/5 rounded-lg overflow-hidden">
      <div className="bg-[#0c0c0e]/50 px-3 py-1.5 border-b border-white/5">
        <span className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-wider">
          {title}
        </span>
      </div>
      <pre className="p-3 text-[10px] font-mono text-zinc-300 overflow-x-auto whitespace-pre-wrap break-all">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}
