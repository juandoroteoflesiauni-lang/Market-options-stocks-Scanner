"use client";
import { useMemo, useState } from "react";
import { DataTable, type Column } from "@/components/panels/DataTable";
import {
  generateOptionsChain,
  type OptionsChainRowFull,
} from "@/data/unusualActivity";

const EXPIRIES = [
  "Jun-20",
  "Jun-27",
  "Jul-18",
  "Jul-25",
  "Aug-15",
  "Sep-19",
  "Dec-19",
];

interface Props {
  underlyingPrice: number;
  onStrikeSelect?: (strike: number) => void;
}

function pct(v: number) {
  return `${(v * 100).toFixed(1)}%`;
}
function num(v: number, d = 2) {
  return v.toFixed(d);
}

export function OptionsChain({ underlyingPrice, onStrikeSelect }: Props) {
  const [expiry, setExpiry] = useState(EXPIRIES[0]);
  const [ivView, setIvView] = useState<"call" | "put" | "combined">("combined");
  const [strikeRange, setStrikeRange] = useState(10);

  const chain = useMemo(
    () => generateOptionsChain(underlyingPrice, expiry),
    [underlyingPrice, expiry],
  );
  const atm = Math.round(underlyingPrice / 5) * 5;
  const filtered = chain.filter(
    (r) => Math.abs(r.strike - atm) <= strikeRange * 5,
  );

  const callColor = (v: number) =>
    v > 0 ? "#00E676" : v < 0 ? "#FF3D5A" : "#8B9AAF";

  const columns: Column<OptionsChainRowFull>[] = [
    // CALL side
    {
      key: "call.oi",
      header: "C OI",
      width: 52,
      align: "right",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
          }}
        >
          {r.call.oi.toLocaleString()}
        </span>
      ),
    },
    {
      key: "call.volume",
      header: "C Vol",
      width: 52,
      align: "right",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
          }}
        >
          {r.call.volume.toLocaleString()}
        </span>
      ),
    },
    {
      key: "call.iv",
      header: "C IV",
      width: 48,
      align: "right",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#FFB800",
          }}
        >
          {pct(r.call.iv)}
        </span>
      ),
    },
    {
      key: "call.delta",
      header: "C Δ",
      width: 44,
      align: "right",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: callColor(r.call.delta),
          }}
        >
          {num(r.call.delta, 3)}
        </span>
      ),
    },
    {
      key: "call.bid",
      header: "Bid",
      width: 48,
      align: "right",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#00C3FF",
          }}
        >
          {num(r.call.bid)}
        </span>
      ),
    },
    {
      key: "call.ask",
      header: "Ask",
      width: 48,
      align: "right",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#00C3FF",
          }}
        >
          {num(r.call.ask)}
        </span>
      ),
    },
    // Strike
    {
      key: "strike",
      header: "Strike",
      width: 64,
      align: "center",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: r.isATM ? 13 : 11,
            fontWeight: r.isATM ? 700 : 400,
            color: r.isATM ? "#00C3FF" : "#E8EDF5",
            padding: r.isATM ? "2px 6px" : undefined,
            background: r.isATM ? "rgba(0,195,255,0.1)" : undefined,
            borderRadius: r.isATM ? 4 : undefined,
            display: "block",
            textAlign: "center",
          }}
        >
          {r.strike}
        </span>
      ),
    },
    // PUT side
    {
      key: "put.bid",
      header: "Bid",
      width: 48,
      align: "left",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#FF7A7A",
          }}
        >
          {num(r.put.bid)}
        </span>
      ),
    },
    {
      key: "put.ask",
      header: "Ask",
      width: 48,
      align: "left",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#FF7A7A",
          }}
        >
          {num(r.put.ask)}
        </span>
      ),
    },
    {
      key: "put.delta",
      header: "P Δ",
      width: 44,
      align: "left",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#FF3D5A",
          }}
        >
          {num(r.put.delta, 3)}
        </span>
      ),
    },
    {
      key: "put.iv",
      header: "P IV",
      width: 48,
      align: "left",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#FFB800",
          }}
        >
          {pct(r.put.iv)}
        </span>
      ),
    },
    {
      key: "put.volume",
      header: "P Vol",
      width: 52,
      align: "left",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
          }}
        >
          {r.put.volume.toLocaleString()}
        </span>
      ),
    },
    {
      key: "put.oi",
      header: "P OI",
      width: 52,
      align: "left",
      render: (r) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
          }}
        >
          {r.put.oi.toLocaleString()}
        </span>
      ),
    },
  ];

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: "var(--radius-lg)",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Expiry tabs */}
      <div
        style={{
          display: "flex",
          gap: 4,
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {EXPIRIES.map((exp) => (
            <button
              key={exp}
              onClick={() => setExpiry(exp)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                padding: "3px 8px",
                border: "1px solid",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                transition: "all 0.15s ease",
                borderColor:
                  expiry === exp ? "#FF3D5A" : "rgba(255,255,255,0.08)",
                background: expiry === exp ? "#FF3D5A15" : "var(--bg-elevated)",
                color: expiry === exp ? "#FF3D5A" : "#4A5568",
              }}
            >
              {exp}
            </button>
          ))}
        </div>
        {/* IV View toggle */}
        <div style={{ display: "flex", gap: 2 }}>
          {(["call", "put", "combined"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setIvView(v)}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                padding: "2px 6px",
                border: "1px solid",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                borderColor:
                  ivView === v ? "#00C3FF" : "rgba(255,255,255,0.06)",
                background: ivView === v ? "#00C3FF15" : "transparent",
                color: ivView === v ? "#00C3FF" : "#4A5568",
                letterSpacing: "0.06em",
                textTransform: "uppercase",
              }}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Strike range slider */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          Strike Range: ±{strikeRange}
        </span>
        <input
          type="range"
          min={4}
          max={14}
          step={1}
          value={strikeRange}
          onChange={(e) => setStrikeRange(Number(e.target.value))}
          style={{ flex: 1, accentColor: "#FF3D5A" }}
        />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
          }}
        >
          ATM: ${atm}
        </span>
      </div>

      {/* Column headers */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto 1fr",
          gap: 4,
          paddingBottom: 4,
          borderBottom: "1px solid rgba(0,195,255,0.15)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#00C3FF",
            letterSpacing: "0.1em",
            textAlign: "center",
          }}
        >
          CALLS
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            letterSpacing: "0.1em",
            width: 64,
            textAlign: "center",
          }}
        >
          STRIKE
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#FF3D5A",
            letterSpacing: "0.1em",
            textAlign: "center",
          }}
        >
          PUTS
        </span>
      </div>

      <DataTable
        columns={columns}
        data={filtered}
        rowKey={(r) => r.strike}
        onRowClick={(r) => onStrikeSelect?.(r.strike)}
        maxHeight={320}
      />
    </div>
  );
}
