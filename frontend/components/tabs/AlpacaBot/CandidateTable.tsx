"use client";

import * as React from "react";
import { TickerLogo } from "@/components/panels/TickerLogo";
import { formatPrice } from "@/utils/format";
import type {
  AlpacaCandidateAnalysis,
  AlpacaDecision,
  AlpacaRoute,
  Suitability,
} from "@/types/alpaca";

const DECISION_COLOR: Record<Suitability, string> = {
  ALLOW: "#00E676",
  SIZE_DOWN: "#FFB800",
  BLOCK: "#FF3D5A",
  INSUFFICIENT_DATA: "#4A5568",
};

interface Props {
  analyses: AlpacaCandidateAnalysis[];
  decisions: AlpacaDecision[];
  selected: string | null;
  onSelect: (symbol: string) => void;
  route1Symbols?: string[];
}

interface Row {
  analysis: AlpacaCandidateAnalysis;
  decision: AlpacaDecision | undefined;
}

function num(value: number | null, decimals = 2): string {
  return value === null ? "—" : value.toFixed(decimals);
}

function HeaderCell({
  children,
  align = "right",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        textAlign: align,
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        color: "#4A5568",
        letterSpacing: "0.08em",
        padding: "6px 8px",
        position: "sticky",
        top: 0,
        background: "var(--bg-panel)",
      }}
    >
      {children}
    </th>
  );
}

export function CandidateTable({
  analyses,
  decisions,
  selected,
  onSelect,
  route1Symbols = [],
}: Props): React.JSX.Element {
  const decisionBySymbol = React.useMemo(() => {
    const map = new Map<string, AlpacaDecision>();
    for (const d of decisions) map.set(d.symbol, d);
    return map;
  }, [decisions]);

  const route1Set = React.useMemo(
    () => new Set(route1Symbols.map((s) => s.toUpperCase())),
    [route1Symbols],
  );

  const isRoute1 = (analysis: AlpacaCandidateAnalysis): boolean =>
    analysis.route === "priority" || route1Set.has(analysis.symbol.toUpperCase());

  const rows: Row[] = React.useMemo(
    () =>
      analyses
        .map((analysis) => ({
          analysis,
          decision: decisionBySymbol.get(analysis.symbol),
        }))
        .sort((a, b) => (b.decision?.score ?? 0) - (a.decision?.score ?? 0)),
    [analyses, decisionBySymbol],
  );

  if (rows.length === 0) {
    return (
      <div
        style={{
          padding: 24,
          textAlign: "center",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "#4A5568",
        }}
      >
        Sin candidatos analizados. Ejecuta un ciclo para poblar el embudo.
      </div>
    );
  }

  return (
    <div style={{ overflowY: "auto", maxHeight: "100%" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <HeaderCell align="left">SÍMBOLO</HeaderCell>
            <HeaderCell>CLOSE</HeaderCell>
            <HeaderCell>ATR</HeaderCell>
            <HeaderCell>MACD</HeaderCell>
            <HeaderCell>RS</HeaderCell>
            <HeaderCell>VOL z</HeaderCell>
            <HeaderCell>RANGO</HeaderCell>
            <HeaderCell>OPT</HeaderCell>
            <HeaderCell>SCORE</HeaderCell>
            <HeaderCell align="left">DECISIÓN</HeaderCell>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ analysis, decision }) => {
            const suitability: Suitability =
              decision?.decision ?? "INSUFFICIENT_DATA";
            const isSelected = analysis.symbol === selected;
            const route: AlpacaRoute | null = isRoute1(analysis)
              ? "priority"
              : analysis.route === "scan"
                ? "scan"
                : null;
            const optScore = analysis.options_confluence?.score;
            return (
              <tr
                key={analysis.symbol}
                onClick={() => onSelect(analysis.symbol)}
                style={{
                  cursor: "pointer",
                  background: isSelected
                    ? "rgba(0,195,255,0.08)"
                    : "transparent",
                  borderBottom: "1px solid rgba(255,255,255,0.04)",
                }}
              >
                <td style={{ padding: "6px 8px" }}>
                  <span
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <TickerLogo symbol={analysis.symbol} size={14} />
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "#E8EDF5",
                      }}
                    >
                      {analysis.symbol}
                    </span>
                    {route === "priority" && (
                      <span
                        style={{
                          padding: "1px 5px",
                          borderRadius: "var(--radius-pill)",
                          fontFamily: "var(--font-mono)",
                          fontSize: 8,
                          color: "#00C3FF",
                          background: "rgba(0,195,255,0.12)",
                          border: "1px solid rgba(0,195,255,0.35)",
                        }}
                      >
                        R1
                      </span>
                    )}
                    {route === "scan" && (
                      <span
                        style={{
                          padding: "1px 5px",
                          borderRadius: "var(--radius-pill)",
                          fontFamily: "var(--font-mono)",
                          fontSize: 8,
                          color: "#8B9AAF",
                          background: "rgba(255,255,255,0.04)",
                          border: "1px solid rgba(255,255,255,0.1)",
                        }}
                      >
                        R2
                      </span>
                    )}
                  </span>
                </td>
                <Cell
                  value={
                    analysis.latest_close === null
                      ? "—"
                      : `$${formatPrice(analysis.latest_close)}`
                  }
                />
                <Cell value={num(analysis.atr)} />
                <Cell
                  value={num(analysis.macd_histogram, 3)}
                  color={
                    (analysis.macd_histogram ?? 0) > 0 ? "#00E676" : "#FF3D5A"
                  }
                />
                <Cell
                  value={num(analysis.relative_strength)}
                  color={
                    (analysis.relative_strength ?? 0) > 0
                      ? "#00E676"
                      : "#FF3D5A"
                  }
                />
                <Cell value={num(analysis.volume_z_score)} />
                <Cell value={num(analysis.close_position_in_range)} />
                <Cell
                  value={
                    route === "priority"
                      ? optScore !== undefined
                        ? `${Math.round(optScore * 100)}%`
                        : "—"
                      : "n/a"
                  }
                  color={
                    optScore !== undefined && optScore >= 0.55
                      ? "#00E676"
                      : optScore !== undefined && optScore < 0.35
                        ? "#FF3D5A"
                        : "#8B9AAF"
                  }
                />
                <Cell value={decision ? decision.score.toFixed(2) : "—"} />
                <td style={{ padding: "6px 8px" }}>
                  <span
                    style={{
                      padding: "2px 8px",
                      borderRadius: "var(--radius-pill)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      fontWeight: 700,
                      color: DECISION_COLOR[suitability],
                      background: `${DECISION_COLOR[suitability]}1A`,
                      border: `1px solid ${DECISION_COLOR[suitability]}55`,
                    }}
                    title={decision?.reason_codes.join(", ") || ""}
                  >
                    {decision
                      ? `${decision.direction} · ${suitability}`
                      : suitability}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Cell({
  value,
  color = "#E8EDF5",
}: {
  value: string;
  color?: string;
}): React.JSX.Element {
  return (
    <td
      style={{
        textAlign: "right",
        padding: "6px 8px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color,
      }}
    >
      {value}
    </td>
  );
}
