"use client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { MetricCard } from "@/components/panels/MetricCard";
import type { PredictiveEngine } from "@/types";
import { categoryColor, confidenceColor, signalColor } from "@/utils/colors";

interface Prediction {
  date: string;
  direction: "BULL" | "BEAR" | "NEUTRAL";
  confidence: number;
  outcome: "CORRECT" | "WRONG" | "PENDING";
  move: number;
}

function mockPredictions(engine: PredictiveEngine): Prediction[] {
  const dirs: Array<"BULL" | "BEAR" | "NEUTRAL"> = ["BULL", "BEAR", "NEUTRAL"];
  return Array.from({ length: 10 }, (_, i) => {
    const seed = (engine.id * 17 + i * 31) % 100;
    const dir = dirs[seed % 3];
    const conf = 40 + ((seed * 7) % 50);
    const correct = seed % 3 !== 2;
    return {
      date: new Date(Date.now() - (10 - i) * 24 * 3_600_000).toLocaleDateString(
        "en-US",
        { month: "short", day: "numeric" },
      ),
      direction: dir,
      confidence: conf,
      outcome: i >= 9 ? "PENDING" : correct ? "CORRECT" : "WRONG",
      move: (dir === "BULL" ? 1 : -1) * (0.5 + (seed % 30) * 0.1),
    };
  });
}

function mockConfidenceCurve(
  engine: PredictiveEngine,
): Array<{ day: number; confidence: number }> {
  return Array.from({ length: 30 }, (_, i) => ({
    day: i + 1,
    confidence: Math.max(
      30,
      engine.confidence - i * 0.3 + Math.sin(i * 0.5) * 5,
    ),
  }));
}

const OUTCOME_COLOR: Record<string, string> = {
  CORRECT: "var(--signal-bull)",
  WRONG: "var(--signal-bear)",
  PENDING: "var(--signal-warn)",
};

const TOOLTIP_STYLE = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--border-muted)",
  borderRadius: 6,
  padding: "6px 10px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "var(--text-primary)",
};

interface Props {
  engine: PredictiveEngine;
}

export function EngineDetail({ engine }: Props) {
  const preds = mockPredictions(engine);
  const curve = mockConfidenceCurve(engine);
  const catCol = categoryColor(engine.category);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Identity */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: catCol,
            background: `${catCol}18`,
            border: `1px solid ${catCol}40`,
            borderRadius: 4,
            padding: "2px 7px",
            letterSpacing: "0.08em",
          }}
        >
          {engine.category}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-muted)",
          }}
        >
          Engine #{String(engine.id).padStart(2, "0")}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color:
              engine.status === "ACTIVE"
                ? "var(--signal-bull)"
                : engine.status === "TRAINING"
                  ? "var(--signal-warn)"
                  : "var(--signal-bear)",
          }}
        >
          ● {engine.status}
        </span>
      </div>

      {/* Accuracy metrics */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 8,
        }}
      >
        <MetricCard
          title="Signal"
          value={engine.signal}
          accentColor={signalColor(engine.signal)}
        />
        <MetricCard
          title="7d Accuracy"
          value={`${engine.accuracy7d}%`}
          accentColor={confidenceColor(engine.accuracy7d)}
        />
        <MetricCard
          title="30d Accuracy"
          value={`${engine.accuracy30d}%`}
          accentColor={confidenceColor(engine.accuracy30d)}
        />
        <MetricCard
          title="90d Accuracy"
          value={`${engine.accuracy90d}%`}
          accentColor={confidenceColor(engine.accuracy90d)}
        />
      </div>

      {/* Confidence curve */}
      <div>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-muted)",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            marginBottom: 8,
          }}
        >
          Confidence Curve (30d)
        </div>
        <div style={{ height: 80 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={curve}
              margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
            >
              <CartesianGrid
                key="grid"
                stroke="var(--border-subtle)"
                strokeDasharray="3 3"
              />
              <XAxis
                key="x-axis"
                dataKey="day"
                tick={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 8,
                  fill: "var(--text-muted)",
                }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                key="y-axis"
                domain={[0, 100]}
                tick={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 8,
                  fill: "var(--text-muted)",
                }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip key="tooltip" contentStyle={TOOLTIP_STYLE} />
              <Line
                key="confidence-line"
                id="predictive-engine-confidence-line"
                dataKey="confidence"
                stroke={catCol}
                strokeWidth={1.5}
                dot={false}
                name="Confidence"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Last 10 predictions */}
      <div>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-muted)",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            marginBottom: 6,
          }}
        >
          Last 10 Predictions
        </div>
        <div
          style={{
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            overflow: "hidden",
          }}
        >
          {preds.map((p, i) => (
            <div
              key={i}
              style={{
                display: "grid",
                gridTemplateColumns: "70px 55px 60px 70px 1fr",
                padding: "5px 10px",
                borderBottom:
                  i < preds.length - 1
                    ? "1px solid var(--border-subtle)"
                    : "none",
                background: i % 2 ? "rgba(255,255,255,0.015)" : "transparent",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                alignItems: "center",
              }}
            >
              <span style={{ color: "var(--text-secondary)" }}>{p.date}</span>
              <span style={{ color: signalColor(p.direction) }}>
                {p.direction}
              </span>
              <span style={{ color: confidenceColor(p.confidence) }}>
                {p.confidence}%
              </span>
              <span style={{ color: OUTCOME_COLOR[p.outcome] }}>
                {p.outcome}
              </span>
              <span
                style={{
                  color:
                    p.move >= 0 ? "var(--signal-bull)" : "var(--signal-bear)",
                  textAlign: "right",
                }}
              >
                {p.move >= 0 ? "+" : ""}
                {p.move.toFixed(2)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
