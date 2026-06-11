"use client";

import * as React from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bug,
  Search,
  Sliders,
  TrendingUp,
  Clock,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuditComplex } from "@/hooks/use-audit-complex";
import { ApiAuditTab } from "./ApiAuditTab";
import { DatabaseAuditTab } from "./DatabaseAuditTab";
import { LogSearchTab } from "./LogSearchTab";
import { ProcessAuditTab } from "./ProcessAuditTab";
import { ErrorDashboard } from "./ErrorDashboard";

type SubTab = "api" | "database" | "logs" | "process" | "errors";

const SUB_TABS: { id: SubTab; label: string; icon: React.ReactNode }[] = [
  { id: "api", label: "API Audit", icon: <Activity className="h-3.5 w-3.5" /> },
  {
    id: "database",
    label: "DB Audit",
    icon: <BarChart3 className="h-3.5 w-3.5" />,
  },
  { id: "logs", label: "Logs", icon: <Search className="h-3.5 w-3.5" /> },
  {
    id: "process",
    label: "Process",
    icon: <Sliders className="h-3.5 w-3.5" />,
  },
  { id: "errors", label: "Errors", icon: <Bug className="h-3.5 w-3.5" /> },
];

export function AuditComplex() {
  const audit = useAuditComplex();
  const [activeSubTab, setActiveSubTab] = React.useState<SubTab>("api");

  const totalErrors = audit.errorStats?.total_errors ?? 0;
  const totalUnresolved = audit.errorStats?.total_unresolved ?? 0;
  const totalLogs = audit.logStats?.total_logs ?? 0;
  const totalCost = audit.dashboard
    ? Object.values(audit.dashboard.api_call_stats).reduce(
        (s, m) => s + m.total_cost_usd,
        0,
      )
    : 0;

  return (
    <div className="min-h-screen bg-[#050506] text-[#f5f5f7] font-sans antialiased p-4 space-y-4">
      {/* Header */}
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-white/5 pb-4">
        <div>
          <span className="text-[9px] text-zinc-500 font-mono uppercase tracking-widest">
            Sistema de Auditoría
          </span>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <Activity className="h-5 w-5 text-amber-500" />
            Audit Complex
          </h1>
          <p className="text-xs text-zinc-500 font-mono mt-0.5">
            Auditoría unificada de APIs, procesos, errores y logs del sistema
          </p>
        </div>

        <div className="flex items-center gap-3">
          <SummaryBadge
            label="Costo"
            value={`$${totalCost.toFixed(4)}`}
            color="text-emerald-400"
          />
          <SummaryBadge
            label="Errores"
            value={String(totalErrors)}
            color={totalUnresolved > 0 ? "text-rose-400" : "text-zinc-400"}
          />
          <SummaryBadge
            label="Logs"
            value={totalLogs.toLocaleString()}
            color="text-zinc-300"
          />
          <button
            onClick={() => void audit.refresh()}
            disabled={audit.isLoading}
            className="p-2 border border-white/5 rounded bg-white/[0.02] hover:bg-white/5 transition-all text-zinc-400 hover:text-white"
            title="Refrescar"
          >
            <RefreshCw
              className={cn("h-4 w-4", audit.isLoading && "animate-spin")}
            />
          </button>
        </div>
      </header>

      {audit.error && (
        <div className="border border-rose-500/20 bg-rose-500/10 text-rose-300 p-3 rounded-xl flex items-center gap-2.5 text-xs font-mono">
          <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0" />
          <span>Error: {audit.error}</span>
        </div>
      )}

      {/* Sub-tab navigation */}
      <nav className="flex items-center gap-1 border-b border-white/5 pb-2">
        {SUB_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveSubTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-mono tracking-wider uppercase transition-all",
              activeSubTab === tab.id
                ? "bg-[#1E2D47] text-[#E8EDF5] border border-[rgba(0,195,255,0.30)] shadow-[0_0_8px_rgba(0,195,255,0.15)]"
                : "text-zinc-500 hover:text-zinc-300 hover:bg-white/[0.02] border border-transparent",
            )}
          >
            {tab.icon}
            {tab.label}
            {tab.id === "errors" && totalUnresolved > 0 && (
              <span className="ml-1 px-1.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 text-[9px] font-bold">
                {totalUnresolved}
              </span>
            )}
          </button>
        ))}
      </nav>

      {/* Sub-tab content */}
      <div className="min-h-[600px]">
        {activeSubTab === "api" && (
          <ApiAuditTab
            apiConsumption={audit.apiConsumption}
            costProjections={audit.costProjections}
            rateLimits={audit.rateLimits}
            isLoading={audit.isLoading}
          />
        )}
        {activeSubTab === "database" && (
          <DatabaseAuditTab fetchSnapshots={audit.fetchSnapshots} />
        )}
        {activeSubTab === "logs" && (
          <LogSearchTab
            logStats={audit.logStats}
            fetchLogs={audit.fetchLogs}
            fetchLogTrace={audit.fetchLogTrace}
          />
        )}
        {activeSubTab === "process" && (
          <ProcessAuditTab
            dashboard={audit.dashboard}
            fetchModuleDetail={audit.fetchModuleDetail}
          />
        )}
        {activeSubTab === "errors" && (
          <ErrorDashboard
            errorStats={audit.errorStats}
            fetchErrors={audit.fetchErrors}
            resolveError={audit.resolveError}
          />
        )}
      </div>
    </div>
  );
}

function SummaryBadge({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="font-mono text-[10px] text-zinc-400 border border-white/5 px-2.5 py-1 rounded bg-white/[0.02]">
      {label}: <strong className={color}>{value}</strong>
    </div>
  );
}
