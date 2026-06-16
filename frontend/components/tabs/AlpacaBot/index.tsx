"use client";

import * as React from "react";
import { AlertTriangle } from "lucide-react";
import { CandleChart } from "@/components/charts/CandleChart";
import { TickerLogo } from "@/components/panels/TickerLogo";
import {
  StaggerContainer,
  StaggerCard,
  staggerContainerProps,
  staggerCardProps,
} from "@/components/layout/TabTransition";
import { useAlpacaBot } from "@/hooks/use-alpaca-bot";
import { AlpacaStatusStrip } from "./AlpacaStatusStrip";
import { FunnelPanel } from "./FunnelPanel";
import { CandidateTable } from "./CandidateTable";
import { IntentsPanel } from "./IntentsPanel";
import { PositionsTable } from "./PositionsTable";
import { ExecutionsLedger } from "./ExecutionsLedger";
import { OptionsConfluencePanel } from "./OptionsConfluencePanel";

const PANEL_STYLE: React.CSSProperties = {
  background: "var(--bg-panel)",
  border: "1px solid rgba(255,255,255,0.06)",
  borderRadius: "var(--radius-xl)",
  padding: 16,
};

const SECTION_LABEL: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  color: "#4A5568",
  letterSpacing: "0.1em",
  marginBottom: 8,
};

export function AlpacaBot(): React.JSX.Element {
  const {
    state,
    isCycling,
    error,
    session,
    equity,
    buyingPower,
    runCycle,
    refresh,
  } = useAlpacaBot();
  const cycle = state.lastCycle;
  const [selectedSymbol, setSelectedSymbol] = React.useState<string | null>(
    null,
  );

  const analyses = cycle?.analyses ?? [];
  const decisions = cycle?.decisions ?? [];
  const riskDecisions = cycle?.risk_decisions ?? [];
  const blockedReasons = cycle?.blocked_reasons ?? {};

  const activeSymbol = selectedSymbol ?? analyses[0]?.symbol ?? null;
  const activeAnalysis =
    analyses.find((a) => a.symbol === activeSymbol) ?? null;
  const activeDecision =
    decisions.find((d) => d.symbol === activeSymbol) ?? undefined;
  const activeIntent =
    riskDecisions.find((r) => r.intent.symbol === activeSymbol)?.intent ?? null;

  return (
    <StaggerContainer
      {...staggerContainerProps}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        height: "100%",
      }}
    >
      <StaggerCard {...staggerCardProps}>
        <AlpacaStatusStrip
          state={state}
          session={session}
          equity={equity}
          buyingPower={buyingPower}
          isCycling={isCycling}
          onRunCycle={(allowLive) => void runCycle(allowLive)}
          onRefresh={() => void refresh()}
        />
      </StaggerCard>

      {error && (
        <StaggerCard {...staggerCardProps}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 14px",
              background: "rgba(255,61,90,0.1)",
              border: "1px solid rgba(255,61,90,0.4)",
              borderRadius: "var(--radius-lg)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#FF3D5A",
            }}
          >
            <AlertTriangle size={13} />
            {error}
          </div>
        </StaggerCard>
      )}

      <StaggerCard {...staggerCardProps}>
        <FunnelPanel cycle={cycle} />
      </StaggerCard>

      <StaggerCard {...staggerCardProps} style={{ flex: 1, minHeight: 0 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "58% 42%",
            gap: 12,
            height: "100%",
          }}
        >
          {/* Left: candidates + chart */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 12,
              minHeight: 0,
            }}
          >
            <div
              style={{
                ...PANEL_STYLE,
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
              }}
            >
              <div style={SECTION_LABEL}>
                CANDIDATOS DEL EMBUDO · ordenados por score
              </div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <CandidateTable
                  analyses={analyses}
                  decisions={decisions}
                  selected={activeSymbol}
                  onSelect={setSelectedSymbol}
                  route1Symbols={cycle?.route1_symbols ?? []}
                />
              </div>
            </div>

            <div style={PANEL_STYLE}>
              <div
                style={{
                  ...SECTION_LABEL,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                {activeSymbol && <TickerLogo symbol={activeSymbol} size={14} />}
                LIVE CHART — {activeSymbol ?? "—"}
              </div>
              <CandleChart
                ticker={activeSymbol ?? "SPY"}
                initialPrice={activeAnalysis?.latest_close ?? 0}
                entryPrice={activeIntent?.reference_price ?? 0}
                takeProfit={activeIntent?.take_profit ?? 0}
                stopLoss={activeIntent?.stop_loss ?? 0}
                height={220}
              />
            </div>
          </div>

          {/* Right: intents + positions + executions */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 12,
              minHeight: 0,
              overflowY: "auto",
            }}
          >
            <div style={PANEL_STYLE}>
              <OptionsConfluencePanel
                analysis={activeAnalysis}
                decision={activeDecision}
                route1Symbols={cycle?.route1_symbols ?? []}
              />
            </div>

            <div style={PANEL_STYLE}>
              <IntentsPanel riskDecisions={riskDecisions} />
            </div>

            <div style={PANEL_STYLE}>
              <div style={SECTION_LABEL}>POSICIONES ABIERTAS</div>
              <PositionsTable positions={state.positions} />
            </div>

            <div style={PANEL_STYLE}>
              <ExecutionsLedger cycle={cycle} />
            </div>

            {Object.keys(blockedReasons).length > 0 && (
              <div style={PANEL_STYLE}>
                <div style={SECTION_LABEL}>MOTIVOS DE BLOQUEO</div>
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 4 }}
                >
                  {Object.entries(blockedReasons).map(([symbol, reasons]) => (
                    <div
                      key={symbol}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 8,
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                      }}
                    >
                      <span style={{ color: "#E8EDF5" }}>{symbol}</span>
                      <span style={{ color: "#8B9AAF", textAlign: "right" }}>
                        {reasons.join(", ")}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </StaggerCard>
    </StaggerContainer>
  );
}
