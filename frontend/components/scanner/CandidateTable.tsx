"use client";

import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { useCandidates } from "@/hooks/useCandidates";

export function CandidateTable() {
  const { candidates, isLoading, error, refetch } = useCandidates();

  return (
    <Card>
      <CardHeader
        title="Phase A — Scanner Candidates"
        subtitle={`${candidates.length} tickers passed validation`}
        action={
          <button
            onClick={refetch}
            disabled={isLoading}
            className={[
              "text-xs px-3 py-1.5 rounded-md",
              "border border-border-default",
              "text-text-secondary hover:text-text-primary",
              "hover:border-border-strong",
              "transition-colors duration-150",
              "disabled:opacity-50 disabled:cursor-not-allowed",
            ].join(" ")}
            aria-label="Refresh candidate list"
          >
            {isLoading ? "Loading..." : "Refresh"}
          </button>
        }
      />

      {error && (
        <div
          role="alert"
          className="text-signal-sell text-xs mb-4 p-3 bg-signal-sell/10 rounded-lg border border-signal-sell/20"
        >
          {error}
        </div>
      )}

      {candidates.length === 0 && !isLoading && !error ? (
        <div className="text-center py-12">
          <p className="text-sm text-text-secondary">No candidates available</p>
          <p className="text-xs text-text-muted mt-1">
            Scanner has not run yet or no tickers passed filters
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="text-left py-2 px-3 text-xs font-medium text-text-muted uppercase tracking-wider">
                  Ticker
                </th>
                <th className="text-left py-2 px-3 text-xs font-medium text-text-muted uppercase tracking-wider">
                  Exchange
                </th>
                <th className="text-right py-2 px-3 text-xs font-medium text-text-muted uppercase tracking-wider">
                  Price
                </th>
                <th className="text-right py-2 px-3 text-xs font-medium text-text-muted uppercase tracking-wider">
                  Volume
                </th>
                <th className="text-left py-2 px-3 text-xs font-medium text-text-muted uppercase tracking-wider">
                  Source
                </th>
                <th className="text-right py-2 px-3 text-xs font-medium text-text-muted uppercase tracking-wider">
                  Latency
                </th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr
                  key={`${c.ticker}-${c.exchange}`}
                  className="border-b border-border-subtle/50 hover:bg-bg-elevated/30 transition-colors duration-100"
                >
                  <td className="py-2.5 px-3">
                    <span className="font-mono font-semibold tracking-wide uppercase text-text-primary">
                      {c.ticker}
                    </span>
                  </td>
                  <td className="py-2.5 px-3">
                    <Badge>{c.exchange}</Badge>
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <span className="font-mono tabular-nums text-text-primary">
                      ${c.price}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <span className="font-mono tabular-nums text-text-secondary">
                      {Number(c.volume).toLocaleString()}
                    </span>
                  </td>
                  <td className="py-2.5 px-3">
                    <span className="text-xs text-text-muted">
                      {c.data_lineage.source}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <span className="font-mono tabular-nums text-xs text-text-muted">
                      {c.data_lineage.ingestion_latency_ms}ms
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
