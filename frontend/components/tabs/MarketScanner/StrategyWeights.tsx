"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import { ExpandableCard } from "@/components/panels/ExpandableCard";
import { WeightSlider } from "@/components/ui/WeightSlider";
import { RiskBar } from "@/components/panels/RiskBar";
import { useTradingStore } from "@/store/tradingStore";
import {
  fetchStrategyWeights,
  bulkUpdateStrategyWeights,
  resetStrategyWeights,
} from "@/services/scannerService";
import type { StrategyWeights as SW } from "@/types";
import type { FlatWeights } from "@/services/scannerService";

type PhaseKey = "phaseA" | "phaseB" | "phaseC" | "phaseD";

const PHASE_META: Record<
  PhaseKey,
  { label: string; ratio: string; color: string; desc: string }
> = {
  phaseA: {
    label: "A — DATA INGESTION",
    ratio: "5,000→300",
    color: "#4A90D9",
    desc: "Gate de validación: ticker, precio, volumen, exchange",
  },
  phaseB: {
    label: "B — MICROSTRUCTURE",
    ratio: "300→20",
    color: "#9B59B6",
    desc: "OFI + SMC + VPIN: orden de flujo y sesgo institucional",
  },
  phaseC: {
    label: "C — DERIVATIVES",
    ratio: "20→5",
    color: "#E67E22",
    desc: "8 motores quant: GEX, GammaFlip, DEX, Flow, 0DTE, ShadowΔ, ΔFlow, Mom",
  },
  phaseD: {
    label: "D — EXECUTION",
    ratio: "5→SEÑAL",
    color: "#2ECC71",
    desc: "Tiempo real: momentum, volatilidad, volumen, VWAP + confluencia",
  },
};

const PHASE_ORDER: PhaseKey[] = ["phaseA", "phaseB", "phaseC", "phaseD"];

export function StrategyWeights() {
  const store = useTradingStore();
  const [local, setLocal] = useState<SW>(() => ({ ...store.strategyWeights }));
  const [applied, setApplied] = useState(false);
  const [activePhase, setActivePhase] = useState<PhaseKey>("phaseC");
  const [syncStatus, setSyncStatus] = useState<
    "synced" | "unsaved" | "syncing" | "error"
  >("synced");
  const [backendError, setBackendError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Hydrate from backend on mount
  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;

    async function hydrate() {
      try {
        const flat = await fetchStrategyWeights(controller.signal);
        if (!controller.signal.aborted && Object.keys(flat).length > 0) {
          const nested = flatToNested(flat);
          store.setStrategyWeights(nested);
          setLocal(structuredClone(nested));
          setSyncStatus("synced");
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") return;
        // Backend unavailable — keep default weights
        setBackendError("Could not load weights from backend");
      }
    }

    hydrate();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalPct = PHASE_ORDER.reduce((s, k) => s + local[k].phaseWeight, 0);

  const handleSlider = useCallback((path: string, value: number) => {
    setLocal((prev) => {
      const next = structuredClone(prev) as unknown as Record<string, unknown>;
      setNested(next, path, value);
      return next as unknown as SW;
    });
    setApplied(false);
    setSyncStatus("unsaved");
  }, []);

  const apply = async () => {
    setSyncStatus("syncing");
    setBackendError(null);
    try {
      const flat = nestedToFlat(local);
      await bulkUpdateStrategyWeights(flat);
      store.setStrategyWeights(local);
      setApplied(true);
      setSyncStatus("synced");
    } catch (err: unknown) {
      setBackendError("Failed to save weights to backend");
      setSyncStatus("error");
      // Still apply locally even if backend fails
      store.setStrategyWeights(local);
      setApplied(true);
    }
  };

  const reset = async () => {
    setSyncStatus("syncing");
    setBackendError(null);
    try {
      const flat = await resetStrategyWeights();
      const nested = flatToNested(flat);
      store.setStrategyWeights(nested);
      setLocal(structuredClone(nested));
      setApplied(false);
      setSyncStatus("synced");
    } catch (err: unknown) {
      // Fallback: reset to code defaults
      store.resetWeights();
      setLocal(structuredClone(store.strategyWeights));
      setApplied(false);
      setSyncStatus("error");
      setBackendError("Failed to reset weights on backend");
    }
  };

  const syncLabel =
    syncStatus === "synced"
      ? "SYNCED"
      : syncStatus === "unsaved"
        ? "UNSAVED"
        : syncStatus === "syncing"
          ? "SYNCING..."
          : "ERROR";
  const syncColor =
    syncStatus === "synced"
      ? "#00E676"
      : syncStatus === "unsaved"
        ? "#FFB800"
        : syncStatus === "syncing"
          ? "#00C3FF"
          : "#FF3D5A";

  return (
    <ExpandableCard
      title="STRATEGY FILTER & WEIGHT"
      subtitle="4-phase asymmetric funnel · weights control pipeline scoring"
      defaultOpen
      accentColor="#00C3FF"
      headerRight={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {backendError && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 9,
                color: "#FF3D5A",
              }}
            >
              {backendError}
            </span>
          )}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              padding: "2px 6px",
              borderRadius: 4,
              background: `${syncColor}15`,
              color: syncColor,
              border: `1px solid ${syncColor}30`,
            }}
          >
            {syncLabel}
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: Math.abs(totalPct - 1.0) < 0.01 ? "#00E676" : "#FFB800",
            }}
          >
            Σ={totalPct.toFixed(2)}
          </span>
        </div>
      }
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          paddingTop: 12,
        }}
      >
        {/* Phase tab selector */}
        <div
          style={{
            display: "flex",
            gap: 4,
            background: "var(--bg-hover)",
            borderRadius: 6,
            padding: 3,
          }}
        >
          {PHASE_ORDER.map((k) => {
            const meta = PHASE_META[k];
            const active = k === activePhase;
            return (
              <button
                key={k}
                onClick={() => setActivePhase(k)}
                style={{
                  flex: 1,
                  padding: "5px 0",
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  letterSpacing: "0.08em",
                  cursor: "pointer",
                  borderRadius: 4,
                  background: active ? meta.color + "22" : "transparent",
                  border: `1px solid ${active ? meta.color + "44" : "transparent"}`,
                  color: active ? meta.color : "#4A5568",
                  transition: "all 0.15s",
                }}
              >
                {k.replace("phase", "P. ")}
              </button>
            );
          })}
        </div>

        {/* Active phase panel */}
        <PhasePanel
          phaseKey={activePhase}
          weights={local}
          onSlider={handleSlider}
        />

        {/* Phase contribution distribution */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.12em",
            }}
          >
            FUNNEL CONTRIBUTION
          </div>
          {PHASE_ORDER.map((k) => {
            const meta = PHASE_META[k];
            const pct = local[k].phaseWeight;
            return (
              <div
                key={k}
                style={{ display: "flex", alignItems: "center", gap: 8 }}
              >
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    color: meta.color,
                    minWidth: 14,
                  }}
                >
                  {k.replace("phase", "")}
                </span>
                <div
                  style={{
                    flex: 1,
                    height: 6,
                    background: "var(--bg-hover)",
                    borderRadius: 3,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: `${pct * 100}%`,
                      background: meta.color,
                      borderRadius: 3,
                      transition: "width 0.3s",
                    }}
                  />
                </div>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    color: "#8B9AAF",
                    minWidth: 32,
                    textAlign: "right",
                  }}
                >
                  {(pct * 100).toFixed(0)}%
                </span>
              </div>
            );
          })}
        </div>

        {/* Global risk + controls */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <RiskBar
            value={totalPct}
            warn={0.95}
            danger={1.05}
            label={`Σ phase weights = ${totalPct.toFixed(2)} / 1.00`}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <button
              onClick={apply}
              style={{
                flex: 1,
                padding: "7px 0",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                letterSpacing: "0.1em",
                cursor: "pointer",
                borderRadius: 6,
                background: applied
                  ? "rgba(0,230,118,0.1)"
                  : "rgba(0,195,255,0.12)",
                border: `1px solid ${applied ? "rgba(0,230,118,0.4)" : "rgba(0,195,255,0.4)"}`,
                color: applied ? "#00E676" : "#00C3FF",
                transition: "all 0.2s",
              }}
            >
              {applied ? "✓ WEIGHTS APPLIED" : "[ APPLY WEIGHTS ]"}
            </button>
            <button
              onClick={reset}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                letterSpacing: "0.08em",
                cursor: "pointer",
                borderRadius: 6,
                padding: "7px 12px",
                background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.08)",
                color: "#8B9AAF",
                transition: "all 0.2s",
              }}
            >
              RESET
            </button>
          </div>
        </div>
      </div>
    </ExpandableCard>
  );
}

// ── Phase Panel (renders sliders for the active phase) ────────

interface PhasePanelProps {
  phaseKey: PhaseKey;
  weights: SW;
  onSlider: (path: string, v: number) => void;
}

function PhasePanel({ phaseKey, weights, onSlider }: PhasePanelProps) {
  const meta = PHASE_META[phaseKey];
  const w = weights[phaseKey];

  switch (phaseKey) {
    case "phaseA": {
      const a = w as SW["phaseA"];
      return (
        <PhaseSection color={meta.color} meta={meta}>
          <WeightSlider
            label="Phase Weight (funnel)"
            value={Math.round(a.phaseWeight * 100)}
            min={0}
            max={30}
            onChange={(v) => onSlider(`${phaseKey}.phaseWeight`, v / 100)}
          />
          <WeightSlider
            label="Validation Strictness"
            value={Math.round(a.validationStrictness * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.validationStrictness`, v / 100)
            }
          />
          <WeightSlider
            label="Min Price ($)"
            value={a.minPrice}
            min={0}
            max={10}
            step={0.1}
            onChange={(v) => onSlider(`${phaseKey}.minPrice`, v)}
          />
          <WeightSlider
            label="Min Volume"
            value={Math.round(a.minVolume / 1000)}
            min={0}
            max={200}
            onChange={(v) => onSlider(`${phaseKey}.minVolume`, v * 1000)}
          />
          <WeightSlider
            label="Max Spread %"
            value={Math.round(a.maxSpreadPct * 100)}
            onChange={(v) => onSlider(`${phaseKey}.maxSpreadPct`, v / 100)}
          />
        </PhaseSection>
      );
    }
    case "phaseB": {
      const b = w as SW["phaseB"];
      return (
        <PhaseSection color={meta.color} meta={meta}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            PHASE WEIGHT · {meta.ratio}
          </div>
          <WeightSlider
            label="Phase Weight (funnel)"
            value={Math.round(b.phaseWeight * 100)}
            min={5}
            max={50}
            onChange={(v) => onSlider(`${phaseKey}.phaseWeight`, v / 100)}
          />
          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.04)",
              margin: "4px 0",
            }}
          />
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            ENGINE WEIGHTS · ∑ ={" "}
            {(b.ofiWeight + b.smcWeight + b.vpinWeight).toFixed(2)}
          </div>
          <WeightSlider
            label="OFI — Order Flow Imbalance"
            value={Math.round(b.ofiWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.ofiWeight`, v / 100)}
          />
          <WeightSlider
            label="SMC — Smart Money Concepts"
            value={Math.round(b.smcWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.smcWeight`, v / 100)}
          />
          <WeightSlider
            label="VPIN — Volume-synchronized PIN"
            value={Math.round(b.vpinWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.vpinWeight`, v / 100)}
          />
          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.04)",
              margin: "4px 0",
            }}
          />
          <WeightSlider
            label="OFI Sensitivity"
            value={b.ofiSensitivity}
            min={0.1}
            max={5.0}
            step={0.1}
            onChange={(v) => onSlider(`${phaseKey}.ofiSensitivity`, v)}
          />
          <WeightSlider
            label="SMC Lookback Periods"
            value={b.smcLookbackPeriods}
            min={5}
            max={100}
            onChange={(v) => onSlider(`${phaseKey}.smcLookbackPeriods`, v)}
          />
        </PhaseSection>
      );
    }
    case "phaseC": {
      const c = w as SW["phaseC"];
      return (
        <PhaseSection color={meta.color} meta={meta}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            PHASE WEIGHT · {meta.ratio}
          </div>
          <WeightSlider
            label="Phase Weight (funnel)"
            value={Math.round(c.phaseWeight * 100)}
            min={20}
            max={70}
            onChange={(v) => onSlider(`${phaseKey}.phaseWeight`, v / 100)}
          />
          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.04)",
              margin: "4px 0",
            }}
          />
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            ENGINE WEIGHTS · 8 MOTORS · ∑ ={" "}
            {(
              c.engineWeights.gexScore +
              c.engineWeights.gammaFlip +
              c.engineWeights.dexExposure +
              c.engineWeights.flowSignal +
              c.engineWeights.zeroDay +
              c.engineWeights.shadowDelta +
              c.engineWeights.deltaFlow
            ).toFixed(2)}
          </div>
          <WeightSlider
            label="GEX — Gamma Exposure"
            value={Math.round(c.engineWeights.gexScore * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.gexScore`, v / 100)
            }
          />
          <WeightSlider
            label="Gamma Flip — Flip Point Proximity"
            value={Math.round(c.engineWeights.gammaFlip * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.gammaFlip`, v / 100)
            }
          />
          <WeightSlider
            label="DEX — Dealer Delta Exposure"
            value={Math.round(c.engineWeights.dexExposure * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.dexExposure`, v / 100)
            }
          />
          <WeightSlider
            label="Flow — Institutional Flow Signal"
            value={Math.round(c.engineWeights.flowSignal * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.flowSignal`, v / 100)
            }
          />
          <WeightSlider
            label="0DTE — Zero-Day Pinning/Cascade"
            value={Math.round(c.engineWeights.zeroDay * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.zeroDay`, v / 100)
            }
          />
          <WeightSlider
            label="Shadow Δ — Shadow Delta Gap"
            value={Math.round(c.engineWeights.shadowDelta * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.shadowDelta`, v / 100)
            }
          />
          <WeightSlider
            label="Δ Flow — Capitulation Detection"
            value={Math.round(c.engineWeights.deltaFlow * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.deltaFlow`, v / 100)
            }
          />
          <WeightSlider
            label="Phase B Momentum — OFI+SMC Confluence"
            value={Math.round(c.engineWeights.phaseBMomentum * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.engineWeights.phaseBMomentum`, v / 100)
            }
          />
          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.04)",
              margin: "4px 0",
            }}
          />
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            CONTRACT SCORE BREAKDOWN · basic=
            {c.contractScoreWeights.basicMetrics.toFixed(2)} · engine=
            {c.contractScoreWeights.engineAverage.toFixed(2)}
          </div>
          <WeightSlider
            label="Basic Metrics Weight (of contract score)"
            value={Math.round(c.contractScoreWeights.basicMetrics * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.contractScoreWeights.basicMetrics`, v / 100)
            }
          />
          <WeightSlider
            label="Min Composite Score (threshold)"
            value={c.contractFilters.minCompositeScore}
            min={0}
            max={100}
            onChange={(v) =>
              onSlider(`${phaseKey}.contractFilters.minCompositeScore`, v)
            }
          />
        </PhaseSection>
      );
    }
    case "phaseD": {
      const d = w as SW["phaseD"];
      return (
        <PhaseSection color={meta.color} meta={meta}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            PHASE WEIGHT · {meta.ratio}
          </div>
          <WeightSlider
            label="Phase Weight (funnel)"
            value={Math.round(d.phaseWeight * 100)}
            min={5}
            max={40}
            onChange={(v) => onSlider(`${phaseKey}.phaseWeight`, v / 100)}
          />
          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.04)",
              margin: "4px 0",
            }}
          />
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              color: "#4A5568",
              letterSpacing: "0.1em",
              marginBottom: 2,
            }}
          >
            TICK ANALYSIS WEIGHTS · ∑ ={" "}
            {(
              d.momentumWeight +
              d.volatilityWeight +
              d.volumeSpikeWeight +
              d.vwapWeight +
              d.phaseCConfluenceWeight
            ).toFixed(2)}
          </div>
          <WeightSlider
            label="Momentum — Price Change %"
            value={Math.round(d.momentumWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.momentumWeight`, v / 100)}
          />
          <WeightSlider
            label="Volatility — Return Std Dev"
            value={Math.round(d.volatilityWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.volatilityWeight`, v / 100)}
          />
          <WeightSlider
            label="Volume Spike — vs Trailing Avg"
            value={Math.round(d.volumeSpikeWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.volumeSpikeWeight`, v / 100)}
          />
          <WeightSlider
            label="VWAP — Distance to VWAP"
            value={Math.round(d.vwapWeight * 100)}
            onChange={(v) => onSlider(`${phaseKey}.vwapWeight`, v / 100)}
          />
          <WeightSlider
            label="Phase C Confluence — Engine Scores"
            value={Math.round(d.phaseCConfluenceWeight * 100)}
            onChange={(v) =>
              onSlider(`${phaseKey}.phaseCConfluenceWeight`, v / 100)
            }
          />
          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.04)",
              margin: "4px 0",
            }}
          />
          <WeightSlider
            label="Entry Momentum Threshold (bps)"
            value={Math.round(d.entryMomentumThreshold * 10000)}
            min={0}
            max={100}
            onChange={(v) =>
              onSlider(`${phaseKey}.entryMomentumThreshold`, v / 10000)
            }
          />
          <WeightSlider
            label="Volume Spike Multiplier (×)"
            value={d.volumeSpikeMultiplier}
            min={1.0}
            max={10.0}
            step={0.1}
            onChange={(v) => onSlider(`${phaseKey}.volumeSpikeMultiplier`, v)}
          />
          <WeightSlider
            label="Min Confidence"
            value={Math.round(d.minConfidence * 100)}
            min={0}
            max={100}
            onChange={(v) => onSlider(`${phaseKey}.minConfidence`, v / 100)}
          />
          <WeightSlider
            label="Cooldown (seconds)"
            value={d.cooldownSeconds}
            min={0}
            max={300}
            onChange={(v) => onSlider(`${phaseKey}.cooldownSeconds`, v)}
          />
          <WeightSlider
            label="Stop Loss %"
            value={Math.round(d.stopLossPct * 100)}
            min={0.1}
            max={10}
            step={0.1}
            onChange={(v) => onSlider(`${phaseKey}.stopLossPct`, v / 100)}
          />
          <WeightSlider
            label="Take Profit %"
            value={Math.round(d.takeProfitPct * 100)}
            min={0.1}
            max={20}
            step={0.1}
            onChange={(v) => onSlider(`${phaseKey}.takeProfitPct`, v / 100)}
          />
        </PhaseSection>
      );
    }
  }
}

// ── Sub-components ────────────────────────────────────────────

function PhaseSection({
  color,
  meta,
  children,
}: {
  color: string;
  meta: (typeof PHASE_META)[PhaseKey];
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        background: `${color}08`,
        borderRadius: 8,
        border: `1px solid ${color}18`,
        padding: 10,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fontWeight: 600,
              color,
              letterSpacing: "0.08em",
            }}
          >
            {meta.label}
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 8,
              color: `${color}88`,
              marginTop: 2,
            }}
          >
            {meta.desc}
          </div>
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            background: `${color}18`,
            color,
            padding: "2px 8px",
            borderRadius: 4,
          }}
        >
          {meta.ratio}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────

function setNested(
  obj: Record<string, unknown>,
  path: string,
  value: number,
): void {
  const parts = path.split(".");
  let cur: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    if (!(p in cur)) return;
    cur = cur[p] as Record<string, unknown>;
  }
  const lastKey = parts[parts.length - 1];
  if (lastKey in cur) {
    cur[lastKey] = value;
  }
}

// ── Backend ↔ Frontend weight conversion ──────────────────────

/**
 * Convert backend flat dict (snake_case, dot-separated) to frontend nested SW.
 * Example: {"phase_a.phase_weight": 0.10} → { phaseA: { phaseWeight: 0.10 } }
 */
function flatToNested(flat: FlatWeights): SW {
  // Start from defaults, then overlay backend values
  const result = structuredClone(DEFAULT_WEIGHTS) as unknown as Record<
    string,
    unknown
  >;

  for (const [key, value] of Object.entries(flat)) {
    const camelPath = key
      .replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
      .replace(/\./g, ".");
    setNested(result, camelPath, value);
  }

  return result as unknown as SW;
}

/**
 * Convert frontend nested SW to backend flat dict (snake_case, dot-separated).
 * Example: { phaseA: { phaseWeight: 0.10 } } → {"phase_a.phase_weight": 0.10}
 */
function nestedToFlat(weights: SW): FlatWeights {
  const flat: FlatWeights = {};

  function walk(obj: Record<string, unknown>, prefix: string) {
    for (const [key, value] of Object.entries(obj)) {
      const snakeKey = key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
      const path = prefix ? `${prefix}.${snakeKey}` : snakeKey;
      if (typeof value === "number") {
        flat[path] = value;
      } else if (typeof value === "object" && value !== null) {
        walk(value as Record<string, unknown>, path);
      }
    }
  }

  walk(weights as unknown as Record<string, unknown>, "");
  return flat;
}

const DEFAULT_WEIGHTS: SW = {
  regimeAdaptationEnabled: true,
  phaseA: {
    phaseWeight: 0.1,
    validationStrictness: 0.85,
    minPrice: 0.5,
    minVolume: 10_000,
    maxSpreadPct: 0.2,
  },
  phaseB: {
    phaseWeight: 0.25,
    ofiWeight: 0.45,
    smcWeight: 0.35,
    vpinWeight: 0.2,
    ofiSensitivity: 1.0,
    smcLookbackPeriods: 20,
    vpinBuckets: 50,
  },
  phaseC: {
    phaseWeight: 0.45,
    engineWeights: {
      gexScore: 0.2,
      gammaFlip: 0.12,
      dexExposure: 0.15,
      flowSignal: 0.12,
      zeroDay: 0.1,
      shadowDelta: 0.1,
      deltaFlow: 0.08,
      phaseBMomentum: 0.13,
    },
    contractScoreWeights: {
      basicMetrics: 0.4,
      engineAverage: 0.6,
      liquidity: 0.375,
      delta: 0.25,
      iv: 0.2,
      dte: 0.175,
    },
    contractFilters: {
      minVolume: 100,
      minOpenInterest: 500,
      maxSpreadPct: 0.15,
      minDte: 14,
      maxDte: 60,
      deltaTargetCall: 0.35,
      deltaTargetPut: -0.35,
      minCompositeScore: 40.0,
      ivMin: 0.1,
      ivMax: 0.4,
      optimalDte: 35,
    },
    topNTickers: 5,
    topNContracts: 5,
  },
  phaseD: {
    phaseWeight: 0.2,
    momentumWeight: 0.35,
    volatilityWeight: 0.25,
    volumeSpikeWeight: 0.2,
    vwapWeight: 0.1,
    phaseCConfluenceWeight: 0.1,
    entryMomentumThreshold: 0.003,
    exitMomentumThreshold: -0.002,
    volumeSpikeMultiplier: 2.5,
    minConfidence: 0.6,
    cooldownSeconds: 30,
    minTicksForSignal: 10,
    stopLossPct: 0.02,
    takeProfitPct: 0.04,
    momentumWindow: 20,
    volatilityWindow: 30,
  },
};
