"use client";

import * as React from "react";
import { ShieldAlert, X } from "lucide-react";

import { useEscapeToClose } from "@/hooks/use-escape-to-close";

interface BingxKillSwitchModalProps {
  onConfirm: (cancelOrders: boolean) => Promise<void> | void;
  onCancel: () => void;
  isSubmitting?: boolean;
}

export function BingxKillSwitchModal({
  onConfirm,
  onCancel,
  isSubmitting = false,
}: BingxKillSwitchModalProps) {
  const [cancelOrders, setCancelOrders] = React.useState(true);
  useEscapeToClose(true, onCancel);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 px-4"
      onClick={onCancel}
    >
      <section
        className="w-full max-w-md border border-bear/55 bg-base shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        aria-label="Kill Switch"
      >
        <header className="flex items-center justify-between border-b border-bear/35 bg-bear/10 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center border border-bear/55 bg-bear/10 text-bear">
              <ShieldAlert className="h-5 w-5" />
            </div>
            <div>
              <div className="q-eyebrow text-bear">Halt ejecucion</div>
              <h2 className="font-mono text-base font-black uppercase text-bear">
                Kill Switch
              </h2>
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="grid h-8 w-8 place-items-center border border-line bg-elevated text-ink-500 hover:bg-hover hover:text-ink-100 disabled:opacity-50"
            aria-label="Cerrar"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-4 p-4">
          <p className="text-sm leading-relaxed text-ink-400">
            Esto intentara cerrar todas las posiciones abiertas del bot.
            Validalo antes de confirmar porque es una accion operativa critica.
          </p>

          <label className="flex items-center gap-3 border border-line bg-elevated p-3 font-mono text-xs uppercase tracking-[0.08em] text-ink-300">
            <input
              type="checkbox"
              checked={cancelOrders}
              onChange={(event) => setCancelOrders(event.target.checked)}
              className="h-4 w-4 accent-bear"
            />
            Cancelar ordenes abiertas primero
          </label>

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={isSubmitting}
              className="h-10 border border-line bg-elevated px-4 font-mono text-[11px] font-bold uppercase text-ink-400 hover:border-line-strong hover:bg-hover hover:text-ink-100 disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              type="button"
              onClick={() => onConfirm(cancelOrders)}
              disabled={isSubmitting}
              className="h-10 border border-bear/65 bg-bear/10 px-4 font-mono text-[11px] font-black uppercase text-bear hover:bg-bear hover:text-void disabled:opacity-50"
            >
              {isSubmitting ? "Ejecutando" : "Confirmar halt"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
