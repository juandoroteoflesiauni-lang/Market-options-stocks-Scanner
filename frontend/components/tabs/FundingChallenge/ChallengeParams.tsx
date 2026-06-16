"use client";
import { CHALLENGE_PRESETS, type ChallengePreset } from "@/data/funding";
import { ChevronDown } from "lucide-react";

interface Props {
  selected: ChallengePreset;
  onSelect: (preset: ChallengePreset) => void;
  accentColor: string;
}

export function ChallengeParams({ selected, onSelect, accentColor }: Props) {
  return (
    <div
      style={{
        background: "rgba(15, 23, 42, 0.4)",
        backdropFilter: "blur(16px)",
        border: "1px solid rgba(255,255,255,0.05)",
        borderRadius: "var(--radius-lg)",
        padding: "16px",
        boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.2)",
      }}
    >
      <span
        style={{  // NOSONAR
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "#8B9AAF",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          display: "block",
          marginBottom: 12,
        }}
      >
        Challenge Configuration
      </span>
      <div style={{ position: "relative" }}>
        <select
          value={selected.id}
          onChange={(e) => {
            const preset = CHALLENGE_PRESETS.find((p) => p.id === e.target.value);
            if (preset) onSelect(preset);
          }}
          style={{
            width: "100%",
            appearance: "none",
            background: "rgba(255,255,255,0.02)",
            border: `1px solid ${accentColor}30`,
            borderRadius: "var(--radius-md)",
            padding: "10px 32px 10px 12px",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            color: "#E8EDF5",
            cursor: "pointer",
            transition: "all 0.2s",
            boxShadow: `inset 0 0 10px ${accentColor}05`,
          }}
        >
          {CHALLENGE_PRESETS.map((p) => (
            <option key={p.id} value={p.id} style={{ background: "#0F172A" }}>
              {p.name}
            </option>
          ))}
        </select>
        <ChevronDown
          size={16}
          color={accentColor}
          style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}
        />
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 12,
          marginTop: 16,
        }}
      >
        {[
          { label: "Account Size", value: `$${(selected.accountSize / 1000).toFixed(0)}K` },
          { label: "Profit Target", value: `$${selected.profitTarget.toLocaleString()}` },
          { label: "Daily Loss Limit", value: `-$${selected.dailyLossLimit.toLocaleString()}` },
          { label: "Max Drawdown", value: `-$${selected.maxDrawdown.toLocaleString()}` },
          { label: "Min Days", value: `${selected.minTradingDays}d` },
          { label: "Max Days", value: `${selected.maxTradingDays}d` },
        ].map((item) => (
          <div key={item.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span
              style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "#8B9AAF", letterSpacing: "0.08em", textTransform: "uppercase" }}
            >
              {item.label}
            </span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600, color: "#E8EDF5" }}>
              {item.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
