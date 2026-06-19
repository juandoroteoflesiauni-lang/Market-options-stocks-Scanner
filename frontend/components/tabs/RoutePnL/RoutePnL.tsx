"use client";

import { useRoutePnL } from "@/hooks/use-route-pnl";

const LABELS: Record<string, string> = {
  R1: "Ruta 1 — Prioritaria",
  R2: "Ruta 2 — Scan",
  BINGX: "BingX Perps",
  OPTIONS_R1: "Opciones R1",
};

function fmtUsd(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}$${value.toFixed(2)}`;
}

export function RoutePnL() {
  const { data, loading, error, refresh } = useRoutePnL();

  if (loading && !data) {
    return (
      <div className="p-6 text-text-secondary font-mono text-sm">
        Cargando PnL por ruta…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 space-y-3">
        <p className="text-signal-sell font-mono text-sm">{error}</p>
        <button
          type="button"
          onClick={() => void refresh()}
          className="px-3 py-1.5 text-xs font-mono border border-border-subtle rounded"
        >
          Reintentar
        </button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">
            PnL por Ruta
          </h1>
          <p className="text-xs text-text-secondary font-mono mt-1">
            Actualizado: {new Date(data.generated_at).toLocaleString()}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="px-3 py-1.5 text-xs font-mono border border-border-subtle rounded hover:bg-bg-elevated"
        >
          Refrescar
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {data.buckets.map((bucket) => (
          <div
            key={bucket.route}
            className="rounded-lg border border-border-subtle bg-bg-surface p-4 space-y-2"
          >
            <h2 className="text-sm font-medium text-text-primary">
              {LABELS[bucket.route] ?? bucket.route}
            </h2>
            <p
              className={`font-mono tabular-nums text-xl ${
                bucket.realized_pnl_usd >= 0
                  ? "text-signal-buy"
                  : "text-signal-sell"
              }`}
            >
              {fmtUsd(bucket.realized_pnl_usd)}
            </p>
            <div className="text-xs font-mono text-text-secondary space-y-1">
              <p>Trades: {bucket.trade_count}</p>
              <p>Ejecuciones: {bucket.execution_count}</p>
              <p>Notional: ${bucket.notional_usd.toFixed(0)}</p>
              <p>
                W/L: {bucket.win_count}/{bucket.loss_count}
              </p>
            </div>
          </div>
        ))}
      </div>

      {data.notes.length > 0 && (
        <div className="rounded-lg border border-border-subtle bg-bg-elevated p-4">
          <h3 className="text-sm font-medium text-text-primary mb-2">
            Notas EOD
          </h3>
          <ul className="text-xs font-mono text-text-secondary space-y-1">
            {data.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      {data.daily.length > 0 && (
        <div className="rounded-lg border border-border-subtle overflow-hidden">
          <table className="w-full text-xs font-mono">
            <thead className="bg-bg-elevated text-text-secondary">
              <tr>
                <th className="text-left p-3">Fecha</th>
                <th className="text-right p-3">Alpaca equity</th>
                <th className="text-right p-3">BingX equity</th>
                <th className="text-right p-3">BingX uPnL</th>
              </tr>
            </thead>
            <tbody>
              {data.daily.map((row) => (
                <tr key={row.date} className="border-t border-border-subtle">
                  <td className="p-3 text-text-primary">{row.date}</td>
                  <td className="p-3 text-right tabular-nums">
                    {row.alpaca_equity_usd?.toFixed(2) ?? "—"}
                  </td>
                  <td className="p-3 text-right tabular-nums">
                    {row.bingx_equity_usdt?.toFixed(2) ?? "—"}
                  </td>
                  <td
                    className={`p-3 text-right tabular-nums ${
                      (row.bingx_unrealized_usdt ?? 0) >= 0
                        ? "text-signal-buy"
                        : "text-signal-sell"
                    }`}
                  >
                    {row.bingx_unrealized_usdt?.toFixed(2) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
