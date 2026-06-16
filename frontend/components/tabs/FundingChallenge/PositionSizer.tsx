"use client";
import { useState, useMemo } from "react";
import { formatCurrency, formatPrice } from "@/utils/format";

interface Props {
  accountSize: number;
  accentColor: string;
}

export function PositionSizer({ accountSize, accentColor }: Props) {
  const [riskPct, setRiskPct] = useState(1.0);
  const [stopPts, setStopPts] = useState(2.5);
  const [entryPrice, setEntryPrice] = useState(200);
  const [direction, setDirection] = useState<"LONG" | "SHORT">("LONG");

  const calc = useMemo(() => {
    const riskAmount = accountSize * (riskPct / 100);
    const shares = Math.floor(riskAmount / stopPts);
    const slPrice = direction === "LONG" ? entryPrice - stopPts : entryPrice + stopPts;
    const tpPrice = direction === "LONG" ? entryPrice + stopPts * 2 : entryPrice - stopPts * 2;
    const positionValue = shares * entryPrice;
    const leverage = positionValue / accountSize;
    return { riskAmount, shares, slPrice, tpPrice, positionValue, leverage };
  }, [riskPct, stopPts, entryPrice, direction, accountSize]);

  const inputStyle: React.CSSProperties = {
    background: "rgba(255,255,255,0.03)",
    border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: "var(--radius-md)",
    padding: "8px 12px",
    fontFamily: "var(--font-mono)",
    fontSize: 13,
    color: "#E8EDF5",
    width: "100%",
    boxShadow: "inset 0 2px 4px rgba(0,0,0,0.1)",
    transition: "border-color 0.2s",
  };

  const labelStyle: React.CSSProperties = {
    fontFamily: "var(--font-mono)",
    fontSize: 9,
    color: "#8B9AAF",
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    display: "block",
    marginBottom: 6,
  };

  const outputRow = (label: string, value: string, highlight = false) => (
    <div
      key={label}
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 0",
        borderBottom: "1px solid rgba(255,255,255,0.03)",
      }}
    >
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "#8B9AAF" }}>{label}</span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: highlight ? 14 : 12,
          fontWeight: highlight ? 700 : 500,
          color: highlight ? accentColor : "#E8EDF5",
        }}
      >
        {value}
      </span>
    </div>
  );

  return (
    <div
      style={{
        background: "rgba(15, 23, 42, 0.4)",
        backdropFilter: "blur(16px)",
        border: `1px solid ${accentColor}30`,
        borderRadius: "var(--radius-lg)",
        padding: "16px",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        boxShadow: `0 8px 32px 0 rgba(0, 0, 0, 0.2), inset 0 0 20px ${accentColor}05`,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#8B9AAF",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        Position Sizing Engine
      </span>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <span style={labelStyle}>Entry Price ($)</span>
          <input
            type="number"
            value={entryPrice}
            onChange={(e) => setEntryPrice(Number(e.target.value))}
            style={inputStyle}
            step={0.5}
          />
        </div>
        <div>
          <span style={labelStyle}>Stop Distance (pts)</span>
          <input
            type="number"
            value={stopPts}
            onChange={(e) => setStopPts(Number(e.target.value))}
            style={inputStyle}
            step={0.25}
            min={0.25}
          />
        </div>
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={labelStyle}>Risk Per Trade</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600, color: accentColor }}>
            {riskPct.toFixed(2)}% · {formatCurrency(calc.riskAmount)}
          </span>
        </div>
        <input
          type="range"
          min={0.25}
          max={3}
          step={0.25}
          value={riskPct}
          onChange={(e) => setRiskPct(Number(e.target.value))}
          style={{ width: "100%", accentColor }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "#4A5568" }}>0.25%</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "#4A5568" }}>3.00%</span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        {(["LONG", "SHORT"] as const).map((d) => (
          <button
            key={d}
            onClick={() => setDirection(d)}
            style={{
              flex: 1,
              padding: "8px 0",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 600,
              letterSpacing: "0.08em",
              border: "1px solid",
              borderRadius: "var(--radius-md)",
              cursor: "pointer",
              transition: "all 0.2s ease",
              borderColor: direction === d ? (d === "LONG" ? "#00E676" : "#FF3D5A") : "rgba(255,255,255,0.08)",
              background: direction === d ? (d === "LONG" ? "rgba(0,230,118,0.15)" : "rgba(255,61,90,0.15)") : "rgba(255,255,255,0.02)",
              color: direction === d ? (d === "LONG" ? "#00E676" : "#FF3D5A") : "#8B9AAF",
              boxShadow: direction === d ? `0 0 12px ${d === "LONG" ? "rgba(0,230,118,0.2)" : "rgba(255,61,90,0.2)"}` : "none",
            }}
          >
            {d}
          </button>
        ))}
      </div>

      <div style={{ borderTop: "1px solid rgba(255,255,255,0.05)", paddingTop: 12 }}>
        {outputRow("Suggested Size", `${calc.shares} shares`, true)}
        {outputRow("Position Value", formatCurrency(calc.positionValue))}
        {outputRow("Stop Loss", `$${formatPrice(calc.slPrice)}`, false)}
        {outputRow("Take Profit", `$${formatPrice(calc.tpPrice)}`)}
        {outputRow("R:R Ratio", "2.00 : 1")}
        {outputRow("Leverage", `${calc.leverage.toFixed(2)}×`)}
      </div>
    </div>
  );
}
