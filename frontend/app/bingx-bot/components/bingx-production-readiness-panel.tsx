"use client";

import * as React from "react";
import { CheckCircle, Clock, RefreshCw, XCircle } from "lucide-react";

import type {
  BingXHealthcheck,
  BingXTelemetry,
  BingXProbeResult,
  BingXAuditCycleSummary,
} from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";
import {
  MetricCell,
  StatusBadge,
  TerminalPanel,
} from "@/components/ui/terminal";

export interface BingxProductionReadinessPanelProps {
  healthcheck: BingXHealthcheck | null;
  telemetry: BingXTelemetry | null;
  cycles: BingXAuditCycleSummary[];
  isLoading?: boolean;
  onRunProbe?: () => void;
}

function GateRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      {ok ? (
        <CheckCircle className="h-3.5 w-3.5 shrink-0 text-bull" aria-hidden />
      ) : (
        <XCircle className="h-3.5 w-3.5 shrink-0 text-bear" aria-hidden />
      )}
      <span
        className={cn(
          "font-mono text-[11px]",
          ok ? "text-ink-300" : "text-bear",
        )}
      >
        {label}
      </span>
      {detail ? (
        <span className="ml-auto font-mono text-[10px] text-ink-500">
          {detail}
        </span>
      ) : null}
    </div>
  );
}

function ProviderRow({ label, present }: { label: string; present: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line py-1 last:border-0">
      <span className="font-mono text-[11px] text-ink-400">{label}</span>
      <span
        className={cn(
          "font-mono text-[11px] font-bold",
          present ? "text-bull" : "text-bear",
        )}
      >
        {present ? "OK" : "MISSING"}
      </span>
    </div>
  );
}

function ProbeRow({
  label,
  probe,
}: {
  label: string;
  probe: BingXProbeResult;
}) {
  const tone =
    probe.status === "ok"
      ? "text-bull"
      : probe.status === "skipped"
        ? "text-ink-500"
        : "text-bear";
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="font-mono text-[10px] text-ink-400">{label}</span>
      <span className={cn("font-mono text-[10px] font-bold", tone)}>
        {probe.status.toUpperCase()}
        {probe.latency_ms != null ? ` ${probe.latency_ms}ms` : ""}
      </span>
    </div>
  );
}

export function BingxProductionReadinessPanel({
  healthcheck,
  telemetry,
  cycles,
  isLoading = false,
  onRunProbe,
}: BingxProductionReadinessPanelProps) {
  const ready = telemetry?.production_ready ?? false;
  const gates = telemetry?.gates;
  const allowlist = telemetry?.gates.allowlist ?? [];
  const hcAgeS = telemetry?.last_probe.age_s;

  const hcGateDetail = gates
    ? hcAgeS != null
      ? `${hcAgeS}s`
      : gates.healthcheck === "NEVER_RUN"
        ? "Nunca ejecutado"
        : gates.healthcheck
    : undefined;

  return (
    <TerminalPanel
      title="Production Readiness"
      eyebrow="Preflight"
      source={
        <div className="flex items-center gap-2">
          {isLoading ? (
            <span className="animate-pulse font-mono text-[10px] uppercase text-ink-500">
              Actualizando...
            </span>
          ) : (
            <StatusBadge tone={ready ? "bull" : "bear"}>
              {ready ? "SISTEMA OPERATIVO (VST)" : "No listo"}
            </StatusBadge>
          )}
          {onRunProbe ? (
            <button
              type="button"
              onClick={onRunProbe}
              disabled={isLoading}
              className="inline-flex h-7 w-7 items-center justify-center border border-line bg-elevated text-ink-400 hover:border-line-strong hover:text-ink-100 disabled:opacity-50"
              title="Ejecutar healthcheck profundo"
            >
              <RefreshCw
                className={cn("h-3.5 w-3.5", isLoading && "animate-spin")}
              />
            </button>
          ) : null}
        </div>
      }
    >
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {/* ── Gates ─────────────────────────────────────────────────────────── */}
        <div>
          <div className="q-eyebrow mb-2">Gates de operacion</div>
          {gates ? (
            <div className="space-y-0.5">
              <GateRow label="Enable live (config)" ok={gates.enable_live} />
              <GateRow
                label="Client configurado live"
                ok={gates.client_configured_live}
              />
              <GateRow
                label="Healthcheck fresco"
                ok={gates.healthcheck === "FRESH"}
                detail={hcGateDetail}
              />
              <GateRow label="Allowlist live" ok={allowlist.length > 0} />
              <GateRow
                label="Paper trading"
                ok={gates.paper_trading}
                detail={gates.paper_trading ? "Activo" : undefined}
              />
              <GateRow label="Audit persistente" ok={gates.audit_persistent} />
              <GateRow
                label="Scheduler configurado"
                ok={gates.scheduler_configured}
              />
              <GateRow
                label="Probe proveedores"
                ok={gates.probe_providers === "OK"}
              />
              <GateRow
                label="Risk desk"
                ok={gates.risk_desk === "OPERATIONAL"}
              />
            </div>
          ) : (
            <div className="font-mono text-[11px] text-ink-500">
              {isLoading ? "Cargando..." : "Sin datos"}
            </div>
          )}

          {/* Allowlist */}
          <div className="mt-3">
            <div className="q-eyebrow mb-1">Allowlist live</div>
            {allowlist.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {allowlist.map((sym) => (
                  <span
                    key={sym}
                    className="border border-line bg-base px-1 font-mono text-[10px] text-ink-300"
                  >
                    {sym}
                  </span>
                ))}
              </div>
            ) : (
              <span className="font-mono text-[11px] text-bear">
                {telemetry ? "Allowlist vacia" : "—"}
              </span>
            )}
          </div>

          {/* Reason codes when blocked */}
          {telemetry && !ready && (
            <div className="mt-3">
              <div className="q-eyebrow mb-1">Motivos de bloqueo</div>
              <div className="flex flex-wrap gap-1">
                {!gates?.enable_live && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    ENABLE_LIVE=false
                  </span>
                )}
                {!gates?.client_configured_live && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    CLIENT_DRY_RUN
                  </span>
                )}
                {gates?.healthcheck !== "FRESH" && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    HEALTHCHECK_STALE
                  </span>
                )}
                {allowlist.length === 0 && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    LIVE_ALLOWLIST_EMPTY
                  </span>
                )}
                {!gates?.paper_trading && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    PAPER_TRADING_ENABLED
                  </span>
                )}
                {!gates?.audit_persistent && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    AUDIT_MEMORY
                  </span>
                )}
                {!gates?.scheduler_configured && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    SCHEDULER_MISSING
                  </span>
                )}
                {gates?.probe_providers !== "OK" && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    PROVIDER_PROBE_FAILED
                  </span>
                )}
                {gates?.risk_desk !== "OPERATIONAL" && (
                  <span className="border border-bear/40 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] text-bear">
                    RISK_DESK_KILL_SWITCH
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Universe counts ───────────────────────────────────────────────── */}
        <div>
          <div className="q-eyebrow mb-2">Universo</div>
          {healthcheck ? (
            <div className="grid grid-cols-2 gap-1">
              <MetricCell
                label="Total"
                value={healthcheck.universe_count}
                tone="neutral"
              />
              <MetricCell
                label="Stock Perps"
                value={
                  healthcheck.stock_perp_count +
                  healthcheck.stock_index_perp_count
                }
                tone="info"
              />
              <MetricCell
                label="L2 Activo"
                value={healthcheck.l2_active_count}
                tone={healthcheck.l2_active_count > 0 ? "bull" : "warn"}
              />
              <MetricCell
                label="L2 Pendiente"
                value={healthcheck.l2_pending_count}
                tone={healthcheck.l2_pending_count > 0 ? "warn" : "neutral"}
              />
              <MetricCell
                label="Options"
                value={healthcheck.options_available_count}
                tone={
                  healthcheck.options_available_count > 0 ? "bull" : "neutral"
                }
              />
              <MetricCell
                label="Predictive"
                value={healthcheck.predictive_available_count}
                tone={
                  healthcheck.predictive_available_count > 0
                    ? "bull"
                    : "neutral"
                }
              />
            </div>
          ) : (
            <div className="font-mono text-[11px] text-ink-500">
              {isLoading ? "Cargando..." : "Sin datos"}
            </div>
          )}
        </div>

        {/* ── Providers ─────────────────────────────────────────────────────── */}
        <div>
          <div className="q-eyebrow mb-2">Proveedores</div>
          {healthcheck ? (
            <div>
              <ProviderRow
                label="BingX API"
                present={healthcheck.providers.bingx_api_key}
              />
              <ProviderRow
                label="FMP API"
                present={healthcheck.providers.fmp_api_key}
              />
              <ProviderRow
                label="Gemini API"
                present={healthcheck.providers.gemini_api_key}
              />
              <ProviderRow
                label="Options creds"
                present={healthcheck.providers.options_credentials}
              />
            </div>
          ) : (
            <div className="font-mono text-[11px] text-ink-500">
              {isLoading ? "Cargando..." : "Sin datos"}
            </div>
          )}

          {healthcheck?.probe_mode &&
            (healthcheck.fmp_probe || healthcheck.options_probe) && (
              <div className="mt-3">
                <div className="q-eyebrow mb-1">Live probes</div>
                {healthcheck.fmp_probe && (
                  <ProbeRow
                    label={`FMP (${healthcheck.fmp_probe.ticker})`}
                    probe={healthcheck.fmp_probe}
                  />
                )}
                {healthcheck.options_probe && (
                  <ProbeRow
                    label={`Options (${healthcheck.options_probe.ticker})`}
                    probe={healthcheck.options_probe}
                  />
                )}
              </div>
            )}
        </div>

        {/* ── Recent cycles ─────────────────────────────────────────────────── */}
        <div>
          <div className="q-eyebrow mb-2">Ciclos recientes</div>
          {cycles.length > 0 ? (
            <div className="divide-y divide-line">
              {cycles.slice(0, 6).map((c) => (
                <div key={c.cycle_id} className="flex items-center gap-2 py-1">
                  <Clock
                    className="h-3 w-3 shrink-0 text-ink-500"
                    aria-hidden
                  />
                  <span className="flex-1 truncate font-mono text-[10px] text-ink-400">
                    {c.started_at.slice(11, 19)} UTC
                  </span>
                  <span
                    className={cn(
                      "font-mono text-[9px] font-bold uppercase",
                      c.dry_run ? "text-info" : "text-bear",
                    )}
                  >
                    {c.dry_run ? "DRY" : "LIVE"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="font-mono text-[11px] text-ink-500">
              Sin ciclos registrados
            </div>
          )}

          {/* L2 probe summary */}
          {healthcheck?.probe_mode &&
            healthcheck.l2_probe_sample_size != null && (
              <div className="mt-3">
                <div className="q-eyebrow mb-1">
                  L2 Probe ({healthcheck.l2_probe_active_count ?? 0}/
                  {healthcheck.l2_probe_sample_size} activos)
                </div>
                {healthcheck.l2_probe_failures?.length ? (
                  <div className="space-y-0.5">
                    {healthcheck.l2_probe_failures.map((f) => (
                      <div key={f.symbol} className="flex items-start gap-1">
                        <XCircle
                          className="mt-0.5 h-3 w-3 shrink-0 text-bear"
                          aria-hidden
                        />
                        <span className="font-mono text-[10px] text-bear">
                          {f.symbol}: {f.reason}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="font-mono text-[10px] text-bull">
                    Todos activos
                  </div>
                )}
              </div>
            )}
        </div>
      </div>
    </TerminalPanel>
  );
}
