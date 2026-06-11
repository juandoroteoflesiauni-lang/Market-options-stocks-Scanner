"use client";

import { ListChecks, Rows3 } from "lucide-react";

import {
  EmptyState,
  SourceBadge,
  TerminalPanel,
} from "@/components/ui/terminal";
import { cn } from "@/lib/utils";
import type {
  BingXExecutionSummary,
  BingXOpenOrder,
  BingXPositionSnapshot,
} from "@/lib/bingx-bot-types";

interface BingxPositionsTableProps {
  positions: BingXPositionSnapshot[];
  executions: BingXExecutionSummary[];
  openOrders: BingXOpenOrder[];
}

export function BingxPositionsTable({
  positions,
  executions,
  openOrders,
}: BingxPositionsTableProps) {
  const hasLivePositions = positions.length > 0;
  const hasOpenOrders = openOrders.length > 0;
  const hasExecutions = executions.length > 0;

  return (
    <TerminalPanel
      title="Posiciones / Ordenes"
      eyebrow={
        <span className="inline-flex items-center gap-1.5">
          <Rows3 className="h-3.5 w-3.5 text-info" />
          Exposicion cuenta
        </span>
      }
      source={
        <span className="inline-flex items-center gap-1.5">
          <ListChecks className="h-3 w-3 text-brass" />
          {positions.length} pos / {openOrders.length} ord
        </span>
      }
      actions={
        hasExecutions ? (
          <SourceBadge>{executions.length} ejecuciones recientes</SourceBadge>
        ) : null
      }
    >
      <div className="space-y-3">
        {hasLivePositions ? (
          <LivePositionsTable positions={positions} />
        ) : (
          <EmptyState
            title="Sin posiciones live"
            description="El bot no reporta exposicion abierta en la cuenta BingX."
          />
        )}

        {hasOpenOrders ? (
          <OpenOrdersTable orders={openOrders} />
        ) : (
          <EmptyState
            title="Sin ordenes abiertas"
            description="No hay ordenes pendientes publicadas en el exchange."
          />
        )}

        {!hasLivePositions && hasExecutions ? (
          <ExecutionFallbackTable executions={executions} />
        ) : null}
      </div>
    </TerminalPanel>
  );
}

function LivePositionsTable({
  positions,
}: {
  positions: BingXPositionSnapshot[];
}) {
  return (
    <TableShell title="Posiciones live">
      <table className="w-full min-w-[860px] font-mono text-[11px]">
        <thead>
          <HeaderRow
            cells={[
              "Simbolo",
              "Lado",
              "Apal.",
              "Entrada",
              "Mark",
              "Tamano",
              "uPnL",
              "Funding",
              "Liq.",
              "Modo",
            ]}
          />
        </thead>
        <tbody>
          {positions.map((position) => {
            const isProfit = position.unrealized_pnl >= 0;
            return (
              <tr
                key={`${position.symbol}-${position.side}`}
                className="border-b border-line transition-colors last:border-0 hover:bg-hover"
              >
                <td className="px-3 py-2 font-black text-ink-100">
                  {position.symbol}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      "font-black uppercase",
                      position.side === "LONG" ? "text-bull" : "text-bear",
                    )}
                  >
                    {position.side}
                  </span>
                </td>
                <td className="px-3 py-2 text-ink-400">{position.leverage}x</td>
                <td className="px-3 py-2 tabular-nums text-ink-300">
                  {position.entry_price.toFixed(4)}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-300">
                  {position.mark_price.toFixed(4)}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-400">
                  {position.size.toFixed(6)}
                </td>
                <td
                  className={cn(
                    "px-3 py-2 font-black tabular-nums",
                    isProfit ? "text-bull" : "text-bear",
                  )}
                >
                  {position.unrealized_pnl >= 0 ? "+" : ""}
                  {position.unrealized_pnl.toFixed(2)}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-400">
                  {position.funding_rate != null
                    ? `${(position.funding_rate * 100).toFixed(4)}%`
                    : "--"}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-500">
                  {position.liquidation_price != null
                    ? position.liquidation_price.toFixed(4)
                    : "--"}
                </td>
                <td className="px-3 py-2">
                  <span className="border border-line bg-base px-1.5 py-0.5 text-[10px] font-bold uppercase text-ink-400">
                    {position.margin_type}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableShell>
  );
}

function OpenOrdersTable({ orders }: { orders: BingXOpenOrder[] }) {
  return (
    <TableShell title="Ordenes abiertas">
      <table className="w-full min-w-[680px] font-mono text-[11px]">
        <thead>
          <HeaderRow
            cells={[
              "Simbolo",
              "Lado",
              "Precio",
              "Cantidad",
              "Status",
              "Venue ID",
            ]}
          />
        </thead>
        <tbody>
          {orders.map((order, index) => {
            const side = (order.side ?? "--").toUpperCase();
            return (
              <tr
                key={`${order.symbol}-${order.venue_order_id ?? index}`}
                className="border-b border-line transition-colors last:border-0 hover:bg-hover"
              >
                <td className="px-3 py-2 font-black text-ink-100">
                  {order.symbol}
                </td>
                <td
                  className={cn(
                    "px-3 py-2 font-black uppercase",
                    side.includes("BUY") || side.includes("LONG")
                      ? "text-bull"
                      : "text-bear",
                  )}
                >
                  {side}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-300">
                  {order.price != null ? order.price.toFixed(4) : "--"}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-400">
                  {order.quantity != null ? order.quantity.toFixed(6) : "--"}
                </td>
                <td className="px-3 py-2">
                  <span className="border border-info/35 bg-info/10 px-1.5 py-0.5 text-[10px] font-bold uppercase text-info">
                    {order.status ?? "OPEN"}
                  </span>
                </td>
                <td className="max-w-[180px] truncate px-3 py-2 text-ink-600">
                  {order.venue_order_id ?? "--"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableShell>
  );
}

function ExecutionFallbackTable({
  executions,
}: {
  executions: BingXExecutionSummary[];
}) {
  return (
    <TableShell title="Ejecuciones recientes">
      <table className="w-full min-w-[760px] font-mono text-[11px]">
        <thead>
          <HeaderRow
            cells={[
              "Simbolo",
              "Lado",
              "Apal.",
              "Entrada",
              "Actual",
              "Notional",
              "P&L",
              "Modo",
              "Hora",
            ]}
          />
        </thead>
        <tbody>
          {executions.map((execution, index) => {
            const pnl =
              execution.entry_price && execution.current_price
                ? ((execution.current_price - execution.entry_price) /
                    execution.entry_price) *
                  execution.notional_usdt *
                  execution.leverage
                : null;
            const isProfit = pnl == null ? null : pnl >= 0;
            const timeStr = execution.timestamp
              ? new Date(execution.timestamp).toISOString().slice(11, 19)
              : "--";

            return (
              <tr
                key={`${execution.symbol}-${execution.timestamp}-${index}`}
                className="border-b border-line transition-colors last:border-0 hover:bg-hover"
              >
                <td className="px-3 py-2 font-black text-ink-100">
                  {execution.symbol}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      "font-black uppercase",
                      execution.side === "BUY" ? "text-bull" : "text-bear",
                    )}
                  >
                    {execution.side === "BUY" ? "LONG" : "SHORT"}
                  </span>
                </td>
                <td className="px-3 py-2 text-ink-400">
                  {execution.leverage}x
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-300">
                  {execution.entry_price?.toFixed(4) ?? "--"}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-300">
                  {execution.current_price?.toFixed(4) ?? "--"}
                </td>
                <td className="px-3 py-2 tabular-nums text-ink-400">
                  {execution.notional_usdt.toFixed(2)} USDT
                </td>
                <td
                  className={cn(
                    "px-3 py-2 font-black tabular-nums",
                    isProfit === null
                      ? "text-ink-500"
                      : isProfit
                        ? "text-bull"
                        : "text-bear",
                  )}
                >
                  {pnl != null
                    ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`
                    : "--"}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      "border px-1.5 py-0.5 text-[10px] font-bold uppercase",
                      execution.dry_run
                        ? "border-info/35 bg-info/10 text-info"
                        : "border-bear/45 bg-bear/10 text-bear",
                    )}
                  >
                    {execution.dry_run ? "DRY RUN" : "LIVE"}
                  </span>
                </td>
                <td className="px-3 py-2 text-ink-600">{timeStr}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableShell>
  );
}

function TableShell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden border border-line bg-base">
      <header className="border-b border-line px-3 py-2 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-ink-500">
        {title}
      </header>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

function HeaderRow({ cells }: { cells: string[] }) {
  return (
    <tr className="border-b border-line bg-elevated text-left text-[10px] uppercase tracking-[0.08em] text-ink-600">
      {cells.map((cell) => (
        <th key={cell} className="px-3 py-2 font-bold">
          {cell}
        </th>
      ))}
    </tr>
  );
}
