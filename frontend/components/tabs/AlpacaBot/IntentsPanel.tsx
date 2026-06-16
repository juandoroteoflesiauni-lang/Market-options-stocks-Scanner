"use client";

import * as React from "react";
import { Target } from "lucide-react";
import { formatCurrency, formatPrice } from "@/utils/format";
import type { EquityRiskDecision } from "@/types/alpaca";

interface Props {
  riskDecisions: EquityRiskDecision[];
}

export function IntentsPanel({ riskDecisions }: Props): React.JSX.Element {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#4A5568",
          letterSpacing: "0.1em",
        }}
      >
        <Target size={12} />
        BRACKET ORDERS · 1x CASH · SL/TP por ATR
      </div>

      {riskDecisions.length === 0 ? (
        <div
          style={{
            padding: 16,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#4A5568",
          }}
        >
          Sin intenciones generadas en el último ciclo.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {riskDecisions.map((rd) => {
            const intent = rd.intent;
            const qty = rd.adjusted_quantity ?? intent.quantity;
            const accent = rd.authorized ? "#00E676" : "#FF3D5A";
            return (
              <div
                key={rd.idempotency_key}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto auto auto auto",
                  alignItems: "center",
                  gap: 12,
                  padding: "8px 12px",
                  background: "rgba(255,255,255,0.03)",
                  border: `1px solid ${accent}33`,
                  borderRadius: "var(--radius-lg)",
                }}
              >
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 2 }}
                >
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                      fontWeight: 600,
                      color: "#E8EDF5",
                    }}
                  >
                    {intent.symbol}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      color: "#8B9AAF",
                    }}
                  >
                    {intent.side} · {qty} sh ·{" "}
                    {formatCurrency(intent.notional_usd, true)}
                  </span>
                </div>
                <Field
                  label="ENTRY"
                  value={`$${formatPrice(intent.reference_price)}`}
                  color="#E8EDF5"
                />
                <Field
                  label="STOP"
                  value={
                    intent.stop_loss === null
                      ? "—"
                      : `$${formatPrice(intent.stop_loss)}`
                  }
                  color="#FF3D5A"
                />
                <Field
                  label="TARGET"
                  value={
                    intent.take_profit === null
                      ? "—"
                      : `$${formatPrice(intent.take_profit)}`
                  }
                  color="#00E676"
                />
                <span
                  style={{
                    padding: "2px 8px",
                    borderRadius: "var(--radius-pill)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    fontWeight: 700,
                    color: accent,
                    background: `${accent}1A`,
                    border: `1px solid ${accent}55`,
                  }}
                  title={rd.reason_codes.join(", ") || ""}
                >
                  {rd.authorized ? "AUTORIZADA" : "BLOQUEADA"}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}): React.JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        textAlign: "right",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 8,
          color: "#4A5568",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 600,
          color,
        }}
      >
        {value}
      </span>
    </div>
  );
}
