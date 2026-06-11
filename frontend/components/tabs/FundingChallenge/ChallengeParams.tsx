// @ts-nocheck
"use client";
import {
  CHALLENGE_PRESETS,
  type ChallengePreset,
  type ChallengeRule,
} from "@/data/funding";
import { RiskBar } from "@/components/panels/RiskBar";
import { ChevronDown, CheckCircle, XCircle, AlertTriangle } from "lucide-react";

interface Props {
  selected: ChallengePreset;
  onSelect: (preset: ChallengePreset) => void;
  rules: ChallengeRule[];
}

const STATUS_ICON = {
  PASS: CheckCircle,
  FAIL: XCircle,
  WARN: AlertTriangle,
} as const;

const STATUS_COLOR = {
  PASS: "#00E676",
  FAIL: "#FF3D5A",
  WARN: "#FFB800",
} as const;

export function ChallengeParams({ selected, onSelect, rules }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Selector */}
      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid rgba(255,255,255,0.06)",
          borderRadius: "var(--radius-lg)",
          padding: "12px 14px",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            display: "block",
            marginBottom: 8,
          }}
        >
          Challenge Preset
        </span>
        <div style={{ position: "relative" }}>
          <select
            value={selected.id}
            onChange={(e) => {
              const preset = CHALLENGE_PRESETS.find(
                (p) => p.id === e.target.value,
              );
              if (preset) onSelect(preset);
            }}
            style={{
              width: "100%",
              appearance: "none",
              background: "var(--bg-elevated)",
              border: "1px solid rgba(0,230,118,0.25)",
              borderRadius: "var(--radius-md)",
              padding: "8px 32px 8px 10px",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "#E8EDF5",
              cursor: "pointer",
            }}
          >
            {CHALLENGE_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <ChevronDown
            size={14}
            color="#4A5568"
            style={{
              position: "absolute",
              right: 10,
              top: "50%",
              transform: "translateY(-50%)",
              pointerEvents: "none",
            }}
          />
        </div>

        {/* Params grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 8,
            marginTop: 12,
          }}
        >
          {[
            {
              label: "Account Size",
              value: `$${(selected.accountSize / 1000).toFixed(0)}K`,
            },
            {
              label: "Profit Target",
              value: `$${selected.profitTarget.toLocaleString()}`,
            },
            {
              label: "Daily Loss Limit",
              value: `-$${selected.dailyLossLimit.toLocaleString()}`,
            },
            {
              label: "Max Drawdown",
              value: `-$${selected.maxDrawdown.toLocaleString()}`,
            },
            { label: "Min Days", value: `${selected.minTradingDays}d` },
            { label: "Max Days", value: `${selected.maxTradingDays}d` },
          ].map((item) => (
            <div
              key={item.label}
              style={{ display: "flex", flexDirection: "column", gap: 2 }}
            >
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  color: "#4A5568",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                }}
              >
                {item.label}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  fontWeight: 600,
                  color: "#E8EDF5",
                }}
              >
                {item.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Rules compliance */}
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
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          Rule Compliance
        </span>

        {rules.map((rule) => {
          const Icon = STATUS_ICON[rule.status];
          const color = STATUS_COLOR[rule.status];
          const ratio =
            rule.unit === "$"
              ? Math.min(1, rule.current / rule.limit)
              : rule.unit === "days"
                ? Math.min(1, rule.current / rule.limit)
                : 0;

          return (
            <div
              key={rule.id}
              style={{ display: "flex", flexDirection: "column", gap: 4 }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <Icon size={12} color={color} />
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: "#E8EDF5",
                    }}
                  >
                    {rule.label}
                  </span>
                </div>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    color,
                    padding: "1px 6px",
                    background: `${color}15`,
                    borderRadius: "var(--radius-sm)",
                    border: `1px solid ${color}30`,
                  }}
                >
                  {rule.status}
                </span>
              </div>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "#4A5568",
                }}
              >
                {rule.description}
              </span>
              {rule.unit === "$" && (
                <RiskBar
                  value={ratio}
                  warn={rule.id === "profit-target" ? 0.5 : 0.7}
                  danger={rule.id === "profit-target" ? 0.9 : 0.9}
                  showValue={false}
                  height={3}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
