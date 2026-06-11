"use client";

import * as React from "react";
import {
  AlertCircle,
  AlertTriangle,
  Activity,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  Zap,
  Shield,
  Database,
  Clock,
  Play,
  Power,
} from "lucide-react";
import { fetchJson } from "@/lib/api-client";
import { useBingxLiveTicker } from "@/hooks/use-bingx-live-ticker";
import type {
  BingXTelemetry,
  BingXTelemetryPosition,
} from "@/lib/bingx-bot-types";

const TICK_VALUE_CLASS =
  "tick-value transition-all duration-200 ease-in-out tabular-nums";

function deriveFsmState(
  side: string | undefined,
  zone: string | undefined,
  isActive: boolean,
): string {
  if (!isActive || !side) return "STANDBY";
  const z = (zone ?? "NEUTRAL").toUpperCase();
  if (side === "LONG") {
    if (z === "ACUMULACION") return "ACCUMULATING_LONG";
    if (z === "DISTRIBUCION") return "FADING_LONG";
    return "LONG_FULL";
  }
  if (z === "DISTRIBUCION") return "ACCUMULATING_SHORT";
  if (z === "ACUMULACION") return "FADING_SHORT";
  return "SHORT_FULL";
}

function formatPnlLeveraged(pos: BingXTelemetryPosition): string {
  const pnl = pos.pnl_real_apalancado;
  if (pnl == null) return "--";
  const sign = pnl >= 0 ? "+" : "";
  return `${sign}${pnl.toFixed(2)}% [${pos.leverage}X]`;
}

export default function BingxBotClient() {
  const [telemetry, setTelemetry] = React.useState<BingXTelemetry | null>(null);
  const [metaError, setMetaError] = React.useState<boolean>(false);
  const [isChangingScheduler, setIsChangingScheduler] = React.useState(false);

  const toggleScheduler = async () => {
    if (!telemetry?.scheduler) return;
    setIsChangingScheduler(true);
    try {
      const isRunning = telemetry.scheduler.state === "running";
      const endpoint = isRunning
        ? "/api/v1/bingx-bot/scheduler/stop"
        : "/api/v1/bingx-bot/scheduler/start";
      const updatedStatus = await fetchJson<any>(endpoint, { method: "POST" });

      setTelemetry((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          scheduler: {
            ...prev.scheduler,
            state:
              updatedStatus.state ||
              updatedStatus.status ||
              (isRunning ? "stopped" : "running"),
            last_cycle_at:
              updatedStatus.last_cycle_at || prev.scheduler.last_cycle_at,
            cycle_count:
              updatedStatus.cycle_count || prev.scheduler.cycle_count,
          },
        };
      });
    } catch (err) {
      console.error("Failed to toggle scheduler:", err);
    } finally {
      setIsChangingScheduler(false);
    }
  };

  const {
    account: liveAccount,
    positions: livePositions,
    positionsBySymbol,
    connected: wsConnected,
    lastTickAt,
    error: wsError,
  } = useBingxLiveTicker(true);

  React.useEffect(() => {
    let isMounted = true;

    const fetchMeta = async () => {
      try {
        const data = await fetchJson<BingXTelemetry>(
          "/api/v1/bingx-bot/telemetry",
          {
            quiet: true,
          },
        );
        if (isMounted) {
          setTelemetry(data);
          setMetaError(false);
        }
      } catch (err) {
        console.error("[Cerebro] Telemetry meta fetch failed:", err);
        if (isMounted) {
          setMetaError(true);
        }
      }
    };

    void fetchMeta();
    const interval = setInterval(fetchMeta, 30_000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const ready = telemetry?.production_ready ?? false;
  const gates = telemetry?.gates;
  const risk = telemetry?.risk_summary;
  const networkError = metaError && !wsConnected;

  const totalEquity = liveAccount.total_equity;
  const availableMargin = liveAccount.available_margin;
  const usedMargin = liveAccount.used_margin;

  const allowlist = telemetry?.universe.allowlist?.length
    ? telemetry.universe.allowlist
    : (telemetry?.universe.symbols ?? []);
  const openSymbols = livePositions.map((p) => p.symbol);
  const universe = [...new Set([...openSymbols, ...allowlist])];

  const totalNotional = risk?.open_positions
    ? Object.values(risk.open_positions).reduce(
        (sum, size) => sum + Math.abs(size),
        0,
      )
    : 0;

  const firewallLimit = availableMargin * 0.15;
  const exposurePct =
    firewallLimit > 0
      ? Math.min(100, (totalNotional / firewallLimit) * 100)
      : 0;

  const tapeLog: Array<{
    time: string;
    msg: string;
    type: "critical" | "warn" | "info";
  }> = [];
  {
    const logs = tapeLog;
    const now = new Date().toLocaleTimeString();

    if (gates) {
      if (gates.probe_providers === "OK") {
        logs.push({
          time: now,
          msg: "SYSTEM_PROBE: Providers Online [OK]",
          type: "info",
        });
      }
      if (gates.risk_desk === "OPERATIONAL") {
        logs.push({
          time: now,
          msg: "RISK_DESK: Operational [OK]",
          type: "info",
        });
      }
      if (gates.healthcheck === "FRESH") {
        logs.push({
          time: now,
          msg: "HEALTHCHECK: Fresh status confirmed [OK]",
          type: "info",
        });
      }
      if (telemetry?.last_probe?.failures?.length) {
        telemetry.last_probe.failures.forEach((f) => {
          logs.push({ time: now, msg: `L2_FAULT: ${f}`, type: "warn" });
        });
      }
    }

    if (risk?.kill_switch_engaged) {
      logs.push({
        time: now,
        msg: `KILL_SWITCH: ${risk.kill_switch_reason ?? "engaged"}`,
        type: "critical",
      });
    }

    if (telemetry?.scheduler?.last_cycle_at) {
      logs.push({
        time: now,
        msg: `CYCLE: last_run=${telemetry.scheduler.last_cycle_at} count=${telemetry.scheduler.cycle_count ?? 0}`,
        type: "info",
      });
    }
  }

  return (
    <div className="min-h-screen bg-[#050506] text-[#f5f5f7] font-sans antialiased overflow-hidden flex flex-col selection:bg-white/20">
      <header className="h-12 border-b border-white/5 bg-[#050506]/60 backdrop-blur-[20px] flex items-center justify-between px-6 z-20 sticky top-0">
        <div className="flex items-center gap-3">
          <div className="h-5 w-5 rounded bg-white/5 border border-white/10 flex items-center justify-center">
            <Zap className="h-3 w-3 text-zinc-100 fill-zinc-100" />
          </div>
          <span className="font-semibold tracking-wider text-xs text-zinc-100">
            QUANTUM ANALYZER TERMINAL
          </span>
          <span className="text-zinc-500 font-mono text-[9px] border border-white/5 px-2 py-0.5 rounded-full bg-white/[0.02]">
            v2.1.0-FSM
          </span>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Clock className="h-3 w-3 text-zinc-500" />
            <span className="font-mono text-[10px] text-zinc-400">
              {lastTickAt ? lastTickAt.toLocaleTimeString() : "--:--:--"}
            </span>
          </div>
          {telemetry?.scheduler?.configured && (
            <>
              <div className="h-3 w-px bg-white/10" />
              <div className="flex items-center gap-2">
                <span className="font-mono text-[9px] text-zinc-500 uppercase">
                  Bot Scheduler:
                </span>
                {telemetry.scheduler.state === "running" ? (
                  <span className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-mono text-[9px] font-bold uppercase">
                    <span className="relative flex h-1 w-1">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-1 w-1 bg-emerald-500"></span>
                    </span>
                    RUNNING
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-rose-500/10 border border-rose-500/20 text-rose-400 font-mono text-[9px] font-bold uppercase">
                    STOPPED
                  </span>
                )}
                <button
                  onClick={toggleScheduler}
                  disabled={isChangingScheduler}
                  className={`flex items-center gap-1 font-mono text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded transition-all border ${
                    telemetry.scheduler.state === "running"
                      ? "bg-rose-500/10 border-rose-500/30 text-rose-400 hover:bg-rose-500/20 active:bg-rose-500/30"
                      : "bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 active:bg-emerald-500/30"
                  } disabled:opacity-50`}
                >
                  {isChangingScheduler ? (
                    <RefreshCw className="h-2.5 w-2.5 animate-spin" />
                  ) : telemetry.scheduler.state === "running" ? (
                    <>
                      <Power className="h-2.5 w-2.5" />
                      APAGAR
                    </>
                  ) : (
                    <>
                      <Play className="h-2.5 w-2.5" />
                      ENCENDER
                    </>
                  )}
                </button>
              </div>
            </>
          )}
          <div className="h-3 w-px bg-white/10" />
          {networkError ? (
            <div className="flex items-center gap-2 px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/20">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-rose-500"></span>
              </span>
              <span className="font-mono text-[9px] text-rose-400 font-semibold uppercase tracking-wider">
                Sync Error
              </span>
            </div>
          ) : wsError ? (
            <div className="flex items-center gap-2 px-2.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20">
              <span className="font-mono text-[9px] text-amber-400 font-semibold uppercase tracking-wider">
                WS DEGRADED
              </span>
            </div>
          ) : wsConnected && ready ? (
            <div className="flex items-center gap-2 px-2.5 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
              <span className="relative flex h-1.5 w-1.5">
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"></span>
              </span>
              <span className="font-mono text-[9px] text-emerald-400 font-semibold uppercase tracking-wider">
                TICK LIVE (VST)
              </span>
            </div>
          ) : ready ? (
            <div className="flex items-center gap-2 px-2.5 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
              <span className="relative flex h-1.5 w-1.5">
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
              </span>
              <span className="font-mono text-[9px] text-emerald-400 font-semibold uppercase tracking-wider">
                LIVE OPERATIONAL (VST)
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 px-2.5 py-0.5 rounded-full bg-zinc-800 border border-zinc-700/50">
              <span className="relative flex h-1.5 w-1.5">
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-zinc-500"></span>
              </span>
              <span className="font-mono text-[9px] text-zinc-400 font-semibold uppercase tracking-wider">
                NO LISTO
              </span>
            </div>
          )}
        </div>
      </header>

      <main className="flex-1 grid grid-cols-1 md:grid-cols-12 gap-2 p-2 bg-[#050506]">
        <section className="col-span-1 md:col-span-3 flex flex-col gap-2">
          <div className="flex-1 rounded-2xl bg-[#121215]/30 border border-white/5 p-5 flex flex-col relative overflow-hidden backdrop-blur-[22px] shadow-[0_24px_50px_rgba(0,0,0,0.5)] transition-all hover:border-white/10">
            <div className="absolute top-0 right-0 p-4 opacity-5 pointer-events-none">
              <Shield className="h-28 w-28 text-white" />
            </div>

            <h2 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-6 flex items-center gap-1.5">
              <Shield className="h-3.5 w-3.5" /> Risk & Capital Desk
            </h2>

            <div className="space-y-6 flex-1">
              <div>
                <div className="text-[9px] text-zinc-500 font-mono tracking-wider mb-1 uppercase">
                  TOTAL EQUITY (USDT)
                </div>
                <div className="text-3xl font-light tracking-tight text-white flex items-baseline gap-1">
                  <span className="text-zinc-500 text-base">$</span>
                  {!wsConnected && !telemetry ? (
                    <RefreshCw className="h-5 w-5 animate-spin text-zinc-600" />
                  ) : (
                    <span
                      className={TICK_VALUE_CLASS}
                      key={`eq-${totalEquity}`}
                    >
                      {totalEquity.toLocaleString("en-US", {
                        minimumFractionDigits: 2,
                      })}
                    </span>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 border-t border-white/5 pt-4">
                <div>
                  <div className="text-[9px] text-zinc-500 font-mono tracking-wider mb-1 uppercase">
                    AVAILABLE MARGIN
                  </div>
                  <div
                    className={`text-base font-mono text-zinc-300 ${TICK_VALUE_CLASS}`}
                  >
                    {!wsConnected && !telemetry ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin text-zinc-600" />
                    ) : (
                      <span key={`am-${availableMargin}`}>
                        {availableMargin.toLocaleString("en-US", {
                          minimumFractionDigits: 2,
                        })}
                      </span>
                    )}
                  </div>
                </div>
                <div>
                  <div className="text-[9px] text-zinc-500 font-mono tracking-wider mb-1 uppercase">
                    USED MARGIN
                  </div>
                  <div
                    className={`text-base font-mono text-zinc-400 ${TICK_VALUE_CLASS}`}
                  >
                    {!wsConnected && !telemetry ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin text-zinc-600" />
                    ) : (
                      <span key={`um-${usedMargin}`}>
                        {usedMargin.toLocaleString("en-US", {
                          minimumFractionDigits: 2,
                        })}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="border-t border-white/5 pt-4">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[9px] text-zinc-500 font-mono tracking-wider uppercase">
                    Max Margin Exposure (15%)
                  </span>
                  <span className="text-[10px] font-mono text-zinc-300 font-bold">
                    {exposurePct.toFixed(1)}%
                  </span>
                </div>
                <div className="h-2 w-full bg-white/5 rounded-full overflow-hidden relative border border-white/5 backdrop-blur-md">
                  <div
                    className={`h-full rounded-full transition-all duration-700 ease-out ${
                      exposurePct > 90
                        ? "bg-gradient-to-r from-rose-500 to-red-600 shadow-[0_0_8px_rgba(239,68,68,0.5)]"
                        : exposurePct > 60
                          ? "bg-gradient-to-r from-amber-500 to-orange-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]"
                          : "bg-gradient-to-r from-emerald-500 to-teal-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]"
                    }`}
                    style={{ width: `${exposurePct}%` }}
                  />
                </div>
                <div className="flex justify-between items-center mt-1.5">
                  <span className="text-[8px] font-mono text-zinc-600">
                    Exposición: ${totalNotional.toFixed(2)}
                  </span>
                  <span className="text-[8px] font-mono text-zinc-600">
                    Límite: ${firewallLimit.toFixed(2)}
                  </span>
                </div>
              </div>

              <div className="border-t border-white/5 pt-4">
                <div className="text-[9px] text-zinc-500 font-mono tracking-wider mb-1 uppercase">
                  UNREALIZED PNL
                </div>
                <div
                  className={`text-xl font-mono flex items-center gap-1.5 ${(risk?.unrealized_pnl_usdt ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}
                >
                  {(risk?.unrealized_pnl_usdt ?? 0) >= 0 ? (
                    <ArrowUpRight className="h-4.5 w-4.5" />
                  ) : (
                    <ArrowDownRight className="h-4.5 w-4.5" />
                  )}
                  {Math.abs(risk?.unrealized_pnl_usdt ?? 0).toLocaleString(
                    "en-US",
                    { minimumFractionDigits: 2 },
                  )}
                </div>
              </div>
            </div>

            <div className="mt-auto pt-4 border-t border-white/5 space-y-2">
              {telemetry?.scheduler?.configured && (
                <div className="bg-[#18181b]/30 rounded-xl border border-white/5 p-3 backdrop-blur-sm flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <div className="text-[8px] text-zinc-500 font-mono uppercase">
                      SCHEDULER STATUS
                    </div>
                    <div
                      className={`text-[10px] font-bold font-mono ${telemetry.scheduler.state === "running" ? "text-emerald-500" : "text-rose-500"}`}
                    >
                      {telemetry.scheduler.state.toUpperCase()}
                    </div>
                  </div>
                  <div className="flex justify-between text-[9px] text-zinc-400 font-mono">
                    <span>Ciclos:</span>
                    <span>{telemetry.scheduler.cycle_count ?? 0}</span>
                  </div>
                  {telemetry.scheduler.last_cycle_at && (
                    <div className="text-[8px] text-zinc-500 font-mono">
                      Último ciclo: {telemetry.scheduler.last_cycle_at}
                    </div>
                  )}
                  <button
                    onClick={toggleScheduler}
                    disabled={isChangingScheduler}
                    className={`w-full py-1.5 rounded-lg border font-mono text-[10px] font-bold uppercase transition-all flex items-center justify-center gap-1.5 ${
                      telemetry.scheduler.state === "running"
                        ? "bg-rose-950/25 border-rose-500/30 text-rose-400 hover:bg-rose-500/20"
                        : "bg-emerald-950/25 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20"
                    } disabled:opacity-50`}
                  >
                    {isChangingScheduler ? (
                      <RefreshCw className="h-3 w-3 animate-spin" />
                    ) : telemetry.scheduler.state === "running" ? (
                      <>
                        <Power className="h-3 w-3" />
                        APAGAR BOT
                      </>
                    ) : (
                      <>
                        <Play className="h-3 w-3" />
                        ENCENDER BOT
                      </>
                    )}
                  </button>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-[#18181b]/30 rounded-xl border border-white/5 p-2 backdrop-blur-sm">
                  <div className="text-[8px] text-zinc-500 font-mono mb-0.5">
                    KILL SWITCH
                  </div>
                  <div
                    className={`text-[10px] font-bold ${risk?.kill_switch_engaged ? "text-rose-500" : "text-emerald-500"}`}
                  >
                    {risk?.kill_switch_engaged ? "ENGAGED" : "DISARMED"}
                  </div>
                </div>
                <div className="bg-[#18181b]/30 rounded-xl border border-white/5 p-2 backdrop-blur-sm">
                  <div className="text-[8px] text-zinc-500 font-mono mb-0.5">
                    PAPER TRADING
                  </div>
                  <div
                    className={`text-[10px] font-bold ${gates?.paper_trading ? "text-amber-500" : "text-zinc-500"}`}
                  >
                    {gates?.paper_trading ? "ACTIVE" : "OFF"}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="col-span-1 md:col-span-6 flex flex-col gap-2">
          <div className="flex-1 rounded-2xl bg-[#121215]/30 border border-white/5 flex flex-col backdrop-blur-[22px] shadow-[0_24px_50px_rgba(0,0,0,0.5)] overflow-hidden transition-all hover:border-white/10">
            <div className="p-4 border-b border-white/5 flex items-center justify-between">
              <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                <Database className="h-3.5 w-3.5" />
                Unified Inventory Matrix
              </h2>
              <div className="flex items-center gap-3">
                <span className="text-[9px] font-mono text-zinc-500">
                  POSITIONS: {livePositions.length}
                </span>
                <span className="text-[9px] font-mono text-zinc-500">
                  UNIVERSE: {telemetry?.universe.total_count ?? 0}
                </span>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-0">
              <table className="w-full text-left border-collapse">
                <thead className="sticky top-0 bg-[#0c0c0e]/95 backdrop-blur-md z-10 border-b border-white/10">
                  <tr>
                    <th className="font-mono text-[8px] text-zinc-500 font-medium py-3 px-4 uppercase w-[16%]">
                      Symbol
                    </th>
                    <th className="font-mono text-[8px] text-zinc-500 font-medium py-3 px-4 uppercase w-[12%] text-right">
                      Spot
                    </th>
                    <th className="font-mono text-[8px] text-zinc-500 font-medium py-3 px-4 uppercase w-[16%] text-center">
                      Zona
                    </th>
                    <th className="font-mono text-[8px] text-zinc-500 font-medium py-3 px-4 uppercase w-[22%] text-center">
                      FSM State
                    </th>
                    <th className="font-mono text-[8px] text-zinc-500 font-medium py-3 px-4 uppercase w-[20%] text-center">
                      PnL Real
                    </th>
                    <th className="font-mono text-[8px] text-zinc-500 font-medium py-3 px-4 uppercase w-[14%] text-right">
                      Open Pos
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {universe.map((sym) => {
                    const pos = positionsBySymbol.get(sym);
                    const isActive = pos != null;
                    const posSize = isActive
                      ? risk?.open_positions?.[sym]
                      : null;
                    const isLong = pos?.side === "LONG";
                    const side = pos?.side;
                    const zone = (pos?.current_zone ?? "NEUTRAL").toUpperCase();
                    const fsmState = deriveFsmState(side, zone, isActive);
                    const pnlLabel = pos ? formatPnlLeveraged(pos) : "--";
                    const pnlValue = pos?.pnl_real_apalancado ?? 0;

                    return (
                      <tr
                        key={sym}
                        className={`group hover:bg-white/[0.02] transition-colors ${isActive ? "bg-white/[0.03]" : ""}`}
                      >
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2">
                            {isActive ? (
                              <div
                                className={`h-1.5 w-1.5 rounded-full ${isLong ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]" : "bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.8)]"}`}
                              />
                            ) : (
                              <div className="h-1.5 w-1.5 rounded-full bg-zinc-800" />
                            )}
                            <div className="flex flex-col">
                              <span
                                className={`font-mono text-xs ${isActive ? "text-white font-bold" : "text-zinc-400"}`}
                              >
                                {sym}
                              </span>
                              {isActive && side && (
                                <span className="text-[8px] text-zinc-600 font-mono">
                                  {side}
                                </span>
                              )}
                            </div>
                          </div>
                        </td>

                        <td className="py-3 px-4 text-right">
                          <span
                            className={`font-mono text-xs text-zinc-300 ${TICK_VALUE_CLASS}`}
                          >
                            {isActive && pos
                              ? pos.current_spot.toFixed(2)
                              : "--"}
                          </span>
                        </td>

                        <td className="py-3 px-4 text-center">
                          {zone === "ACUMULACION" ? (
                            <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-[9px] font-mono border border-emerald-500/20 bg-emerald-500/10 text-emerald-400 font-medium">
                              ACUMULACIÓN
                            </span>
                          ) : zone === "DISTRIBUCION" ? (
                            <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-[9px] font-mono border border-rose-500/20 bg-rose-500/10 text-rose-400 font-medium">
                              DISTRIBUCIÓN
                            </span>
                          ) : (
                            <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-[9px] font-mono border border-zinc-700/30 bg-zinc-800/30 text-zinc-400 font-medium">
                              {isActive ? "NEUTRAL" : "--"}
                            </span>
                          )}
                        </td>

                        <td className="py-3 px-4 text-center">
                          {fsmState === "ACCUMULATING_LONG" ||
                          fsmState === "ACCUMULATING_SHORT" ? (
                            <div className="inline-flex items-center gap-1 bg-emerald-500/5 px-2 py-0.5 border border-emerald-500/10 rounded-lg">
                              <span className="relative flex h-1.5 w-1.5">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
                              </span>
                              <span className="text-[9px] font-mono text-emerald-400 font-bold uppercase animate-pulse">
                                {fsmState}
                              </span>
                            </div>
                          ) : fsmState === "LONG_FULL" ||
                            fsmState === "SHORT_FULL" ? (
                            <span className="inline-flex items-center gap-1 bg-amber-500/10 border border-amber-500/20 text-amber-400 font-mono font-bold text-[8px] px-2 py-0.5 rounded-lg tracking-wide select-none">
                              {fsmState}
                            </span>
                          ) : fsmState === "FADING_LONG" ||
                            fsmState === "FADING_SHORT" ? (
                            <span className="inline-flex items-center gap-1 bg-blue-500/10 border border-blue-500/20 text-blue-400 font-mono font-bold text-[9px] px-2 py-0.5 rounded-lg animate-pulse">
                              {fsmState}
                            </span>
                          ) : (
                            <span className="text-[9px] font-mono text-zinc-600 tracking-wider">
                              IDLE / STANDBY
                            </span>
                          )}
                        </td>

                        <td className="py-3 px-4 text-center">
                          {isActive && pos?.pnl_real_apalancado != null ? (
                            <span
                              className={`font-mono text-xs font-bold tick-value transition-all duration-200 ease-in-out ${pnlValue >= 0 ? "text-emerald-400" : "text-rose-400"}`}
                            >
                              {pnlLabel}
                            </span>
                          ) : (
                            <span className="font-mono text-[9px] text-zinc-700">
                              --
                            </span>
                          )}
                        </td>

                        <td className="py-3 px-4 text-right">
                          {isActive && posSize != null ? (
                            <span
                              className={`font-mono text-xs font-semibold ${isLong ? "text-emerald-400" : "text-rose-400"}`}
                            >
                              $
                              {Math.abs(posSize).toLocaleString("en-US", {
                                minimumFractionDigits: 2,
                              })}
                            </span>
                          ) : (
                            <span className="font-mono text-[9px] text-zinc-700">
                              --
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section className="col-span-1 md:col-span-3 flex flex-col gap-2">
          <div className="flex-1 rounded-2xl bg-[#121215]/30 border border-white/5 flex flex-col backdrop-blur-[22px] shadow-[0_24px_50px_rgba(0,0,0,0.5)] overflow-hidden transition-all hover:border-white/10">
            <div className="p-4 border-b border-white/5 flex items-center justify-between">
              <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                <Activity className="h-3.5 w-3.5 text-zinc-500" />
                Cerebro Tape
              </h2>
            </div>

            <div className="flex-1 p-4 overflow-y-auto custom-scrollbar font-mono text-[10px] space-y-3 bg-black/10">
              {tapeLog.length === 0 && (
                <div className="flex items-center gap-2 text-zinc-600">
                  <AlertCircle className="h-3.5 w-3.5" />
                  <span>Esperando telemetría del backend…</span>
                </div>
              )}
              {tapeLog.map((log, i) => {
                const isCritical = log.type === "critical";
                if (isCritical) {
                  return (
                    <div
                      key={i}
                      className="border-l-4 border-l-rose-600 bg-rose-950/20 border border-rose-500/20 text-rose-300 p-3 rounded-xl flex flex-col gap-1.5 shadow-lg"
                    >
                      <div className="flex items-center gap-1.5 text-rose-400 font-semibold text-[9px] uppercase tracking-wider">
                        <AlertTriangle className="h-3.5 w-3.5 text-rose-500" />
                        CRITICAL
                      </div>
                      <span className="text-zinc-400 text-[8px]">
                        {log.time}
                      </span>
                      <span className="text-rose-200 font-bold leading-normal">
                        {log.msg}
                      </span>
                    </div>
                  );
                }
                return (
                  <div
                    key={i}
                    className="flex flex-col gap-0.5 border-l-2 border-white/10 pl-2 opacity-80 hover:opacity-100 transition-opacity"
                  >
                    <span className="text-zinc-600">{log.time}</span>
                    <span
                      className={
                        log.type === "warn"
                          ? "text-amber-400"
                          : log.type === "critical"
                            ? "text-rose-400"
                            : "text-zinc-300"
                      }
                    >
                      {log.msg}
                    </span>
                  </div>
                );
              })}

              <div className="flex flex-col gap-0.5 border-l-2 border-emerald-500 pl-2">
                <span className="text-zinc-600">
                  {lastTickAt?.toLocaleTimeString() ?? "--:--"}
                </span>
                <span className="text-emerald-400">
                  {wsConnected
                    ? "LIVE_TICKER: WebSocket stream active."
                    : "LIVE_TICKER: reconnecting…"}
                </span>
              </div>
            </div>
          </div>
        </section>
      </main>

      <style
        dangerouslySetInnerHTML={{
          __html: `
        .tick-value {
          transition: all 0.2s ease-in-out;
        }
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
