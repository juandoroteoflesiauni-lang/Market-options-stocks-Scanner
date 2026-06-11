"use client";

import * as React from "react";
import {
  Bot,
  CircleDollarSign,
  Power,
  Radio,
  ShieldAlert,
  WalletCards,
} from "lucide-react";

import type { BingXAccountState } from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";
import { MetricCell, StatusBadge } from "@/components/ui/terminal";

import { BingxKillSwitchModal } from "./bingx-kill-switch-modal";
import { BingxModeSwitch } from "./bingx-mode-switch";

interface BingxCommandDeckProps {
  connected: boolean;
  dryRun: boolean;
  balanceUsdt: number;
  account: BingXAccountState | null;
  positionCount: number;
  lastCycleAt: string | null;
  onModeToggle: (live: boolean) => void;
  onKillSwitch: (cancelOrders: boolean) => Promise<void> | void;
  liveReady?: boolean;
}

export function BingxCommandDeck({
  connected,
  dryRun,
  balanceUsdt,
  account,
  positionCount,
  lastCycleAt,
  onModeToggle,
  onKillSwitch,
  liveReady = false,
}: BingxCommandDeckProps) {
  const [showKillModal, setShowKillModal] = React.useState(false);
  const [isLive, setIsLive] = React.useState(!dryRun);
  const [isKilling, setIsKilling] = React.useState(false);

  React.useEffect(() => {
    setIsLive(!dryRun);
  }, [dryRun]);

  const handleToggle = (live: boolean) => {
    setIsLive(live);
    onModeToggle(live);
  };

  const handleKillConfirm = async (cancelOrders: boolean) => {
    setIsKilling(true);
    try {
      await onKillSwitch(cancelOrders);
      setShowKillModal(false);
    } finally {
      setIsKilling(false);
    }
  };

  return (
    <>
      <section className="border border-line bg-elevated">
        <div className="flex flex-wrap items-center gap-3 border-b border-line bg-base px-4 py-3">
          <div className="grid h-10 w-10 place-items-center border border-brass/50 bg-brass/10 text-brass">
            <Bot className="h-5 w-5" />
          </div>
          <div className="min-w-[180px]">
            <div className="q-eyebrow">Modulo ejecucion</div>
            <h1 className="text-lg font-black uppercase tracking-[0.08em] text-ink-100">
              BingX Bot
            </h1>
            <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-500">
              Perps / micro cuenta
            </p>
          </div>

          <StatusBadge tone={connected ? "bull" : "bear"}>
            <Radio className="h-3 w-3" />
            {connected ? "Conectado" : "Desconectado"}
          </StatusBadge>
          <StatusBadge tone={isLive ? "bear" : "info"}>
            <Power className="h-3 w-3" />
            {isLive ? "Ejecucion live" : "Dry run"}
          </StatusBadge>
          <StatusBadge tone="neutral">
            {lastCycleAt
              ? `Ultimo ${new Date(lastCycleAt).toISOString().slice(11, 19)} UTC`
              : "Ciclo pendiente"}
          </StatusBadge>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <BingxModeSwitch
              isLive={isLive}
              onToggle={handleToggle}
              liveReady={liveReady}
            />
            <button
              type="button"
              disabled={!isLive || isKilling}
              onClick={() => setShowKillModal(true)}
              className={cn(
                "inline-flex h-9 items-center gap-2 border px-3 font-mono text-[11px] font-bold uppercase transition-colors",
                isLive
                  ? "border-bear/60 bg-bear/10 text-bear hover:bg-bear hover:text-void"
                  : "cursor-not-allowed border-line bg-elevated text-ink-600",
              )}
              title={!isLive ? "Disponible solo en modo LIVE" : "Kill Switch"}
            >
              <ShieldAlert className="h-3.5 w-3.5" />
              Kill Switch
            </button>
          </div>
        </div>

        <div className="grid gap-2 p-3 md:grid-cols-3 xl:grid-cols-6">
          <MetricCell
            label="Equity"
            value={`${balanceUsdt.toFixed(2)} USDT`}
            detail="Valor total cuenta"
            tone="bull"
          />
          <MetricCell
            label="Disponible"
            value={(account?.available_margin_usdt ?? balanceUsdt).toFixed(2)}
            detail="Margen listo"
          />
          <MetricCell
            label="Margen usado"
            value={(account?.used_margin_usdt ?? 0).toFixed(2)}
            detail="Exposicion activa"
            tone={(account?.used_margin_usdt ?? 0) > 0 ? "warn" : "neutral"}
          />
          <MetricCell
            label="uPnL"
            value={`${(account?.unrealized_pnl_usdt ?? 0) >= 0 ? "+" : ""}${(account?.unrealized_pnl_usdt ?? 0).toFixed(2)}`}
            detail="Posiciones abiertas"
            tone={(account?.unrealized_pnl_usdt ?? 0) >= 0 ? "bull" : "bear"}
          />
          <MetricCell
            label="Posiciones"
            value={positionCount}
            detail="Contratos abiertos"
            tone={positionCount > 0 ? "info" : "neutral"}
          />
          <MetricCell
            label="Margin Ratio"
            value={
              account?.margin_ratio == null
                ? "--"
                : `${(account.margin_ratio * 100).toFixed(1)}%`
            }
            detail="Presion cuenta"
            tone={
              (account?.margin_ratio ?? 0) > 0.65
                ? "bear"
                : (account?.margin_ratio ?? 0) > 0.35
                  ? "warn"
                  : "neutral"
            }
          />
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-base px-3 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-500">
          <WalletCards className="h-3.5 w-3.5 text-brass" />
          <span>Poll 30s</span>
          <span className="h-4 w-px bg-line" />
          <CircleDollarSign className="h-3.5 w-3.5 text-live" />
          <span>
            Realizado hoy {(account?.realized_pnl_today_usdt ?? 0).toFixed(2)}{" "}
            USDT
          </span>
          <span className="h-4 w-px bg-line" />
          <span>
            Posicion mayor{" "}
            {account?.largest_position_pct == null
              ? "--"
              : `${(account.largest_position_pct * 100).toFixed(1)}%`}
          </span>
        </div>
      </section>

      {showKillModal && (
        <BingxKillSwitchModal
          onConfirm={handleKillConfirm}
          onCancel={() => setShowKillModal(false)}
          isSubmitting={isKilling}
        />
      )}
    </>
  );
}
