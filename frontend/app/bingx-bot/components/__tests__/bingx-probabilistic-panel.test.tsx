/**
 * Render tests for `BingxProbabilisticPanel`.
 *
 * Survival contract enforced by this panel:
 *   - When `ok=false`, no probability bar is rendered (operator MUST NOT see
 *     a confident-looking distribution backed by missing data).
 *   - When `source="equity_heuristic"`, the badge MUST signal a degraded
 *     source (tone "warn" + literal "Heuristico").
 *   - When `source="meta_learner"`, the badge MUST signal a trusted source
 *     (tone "info" + literal "Meta-Learner"), distinct from the heuristic.
 *   - When `probabilistic` is null (e.g. crypto path), the component must
 *     not crash and must still surface a "No disponible" affordance so the
 *     UI never silently hides the absence of evidence.
 *
 * We do NOT mock the component or its helpers — only fixture data goes in.
 * Anchoring on text/role rather than className means a style refactor will
 * not produce false negatives, while a contract regression still fails.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BingxProbabilisticPanel } from "@/app/bingx-bot/components/bingx-probabilistic-panel";
import type {
  BingXPredictiveSignal,
  EquityProbabilistic,
} from "@/lib/bingx-bot-types";

describe("BingxProbabilisticPanel", () => {
  // -------------------------------------------------------------------------
  // Fixture 1 — ok=true, source="equity_heuristic"
  // The degraded-source path: real numbers must render, but the badge must
  // mark the source as heuristic so the operator never confuses it with a
  // trained model output.
  // -------------------------------------------------------------------------
  it("renders heuristic badge, bull probability and confidence/coverage when ok", () => {
    const prob: EquityProbabilistic = {
      ok: true,
      ticker: "GOOGL",
      bull_probability: 0.62,
      bear_probability: 0.24,
      neutral_probability: 0.14,
      confidence: 0.71,
      source: "equity_heuristic",
      features: {
        rsi_14: 58.3,
        momentum_10: 2.8,
        return_zscore_20: 0.9,
        atr_norm_14: 1.4,
        coverage: 1.0,
      },
    };

    render(<BingxProbabilisticPanel probabilistic={prob} />);

    // Source badge — the literal in the component is "Heuristico" (no accent).
    expect(screen.getByText(/Heuristico/i)).toBeInTheDocument();

    // Bull probability footer carries the "Bull 62%" literal. The bar
    // segment also renders "62%" but we anchor on the footer label which
    // is the operator-facing line.
    expect(screen.getByText(/Bull\s+62%/i)).toBeInTheDocument();

    // Confidence and Coverage labels must be visible (they're score cells
    // and operators rely on them being legibly labeled).
    expect(screen.getByText(/^Confidence$/i)).toBeInTheDocument();
    expect(screen.getByText(/^Coverage$/i)).toBeInTheDocument();

    // Confidence renders as 71% (0.71 rounded). Coverage as 100% (1.0).
    expect(screen.getByText(/^71%$/)).toBeInTheDocument();
    expect(screen.getByText(/^100%$/)).toBeInTheDocument();

    // No "No disponible" block in the happy path.
    // (Header text "Senales probabilisticas" lives in the section header,
    // but the unavailable block uses the same string "No disponible".)
    expect(
      screen.queryByText(/No disponible/i, { selector: "div" }),
    ).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Fixture 2 — ok=false with reason. The probability bar MUST NOT render;
  // the operator-facing reason MUST surface so degradation is explicit.
  // -------------------------------------------------------------------------
  it("renders unavailable block with reason when ok=false", () => {
    const prob: EquityProbabilistic = {
      ok: false,
      ticker: "GOOGL",
      source: "equity_heuristic",
      reason: "low_data_quality",
    };
    const errorReason = "UNAVAILABLE: low_data_quality";

    render(
      <BingxProbabilisticPanel
        probabilistic={prob}
        errorReason={errorReason}
      />,
    );

    // The unavailable block heading uses the literal "No disponible".
    expect(
      screen.getByText(/^No disponible$/i, { selector: "div" }),
    ).toBeInTheDocument();

    // The error reason passed in via `errorReason` takes precedence over
    // `probabilistic.reason` and must be displayed verbatim so operators
    // can correlate with backend logs.
    expect(
      screen.getByText(/UNAVAILABLE: low_data_quality/),
    ).toBeInTheDocument();

    // Probability footer (`Bull XX%`) MUST NOT render — this is the
    // survival-critical assertion: a degraded engine cannot pretend to
    // have a distribution.
    expect(screen.queryByText(/Bull\s+\d+%/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Bear\s+\d+%/i)).not.toBeInTheDocument();

    // Score cells (Confidence / Coverage) must not render either.
    expect(screen.queryByText(/^Confidence$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Coverage$/i)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Fixture 3 — probabilistic=null. Crypto path: the panel must render
  // without crashing and still surface a "No disponible" affordance so the
  // operator knows evidence is absent (rather than the panel being silent).
  // -------------------------------------------------------------------------
  it("renders unavailable block when probabilistic is null", () => {
    render(<BingxProbabilisticPanel probabilistic={null} />);

    // Header still renders (component does not collapse to null).
    expect(screen.getByText(/Senales probabilisticas/i)).toBeInTheDocument();

    // Unavailable block heading present — operator sees explicit absence.
    expect(
      screen.getByText(/^No disponible$/i, { selector: "div" }),
    ).toBeInTheDocument();

    // Default reason when none supplied is "insufficient_data" (per the
    // UnavailableBlock fallback).
    expect(screen.getByText(/insufficient_data/i)).toBeInTheDocument();

    // No distribution rendered.
    expect(screen.queryByText(/Bull\s+\d+%/i)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Fixture 4 — ok=true, source="meta_learner". The trusted-source badge
  // must be distinct from the heuristic one, both in text and in tone.
  // -------------------------------------------------------------------------
  it("renders Meta-Learner badge (info tone) distinct from heuristic", () => {
    const prob: EquityProbabilistic = {
      ok: true,
      ticker: "GOOGL",
      bull_probability: 0.7,
      bear_probability: 0.2,
      neutral_probability: 0.1,
      confidence: 0.85,
      source: "meta_learner",
    };

    render(<BingxProbabilisticPanel probabilistic={prob} />);

    // Badge text — note the hyphen; matches the literal in SourceBadge.
    const badge = screen.getByText(/Meta-Learner/i);
    expect(badge).toBeInTheDocument();

    // The heuristic literal must NOT be present in this branch.
    expect(screen.queryByText(/Heuristico/i)).not.toBeInTheDocument();

    // Tone differentiation: meta_learner uses tone="info", heuristic uses
    // tone="warn". The StatusBadge bakes the tone into the className
    // (`text-info` vs `text-warn`). We assert the className signature to
    // catch a silent tone swap that would dilute the source-quality signal.
    expect(badge.className).toMatch(/text-info/);
    expect(badge.className).not.toMatch(/text-warn/);

    // Bull footer renders at 70%.
    expect(screen.getByText(/Bull\s+70%/i)).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Fixture 5 — Bridge signal from meta-signal source. When the backend
  // returns a normalised ``predictive_signal``, the panel must:
  //   - Render a LONG/SHORT/NEUTRAL bias chip distinct from the probability
  //     bar so operators see the institutional direction call.
  //   - Tag the header badge with "Meta-Signal" (info tone) — the trusted
  //     source label distinct from the heuristic fallback.
  //   - Surface ``reason_codes`` so the fallback provenance is visible.
  // -------------------------------------------------------------------------
  it("renders bridge signal block with bias chip + meta-signal badge + reason codes", () => {
    const signal: BingXPredictiveSignal = {
      directional_bias: "LONG",
      probability_long: 0.62,
      probability_short: 0.18,
      confidence: 0.72,
      horizon: "intraday",
      source: "meta_signal",
      quality_score: 0.55,
      reason_codes: ["regime:RISK_ON", "conviction:HIGH"],
    };

    render(<BingxProbabilisticPanel probabilistic={null} signal={signal} />);

    // Header badge — Meta-Signal label (NOT the heuristic literal).
    const badge = screen.getByText(/Meta-Signal/i);
    expect(badge).toBeInTheDocument();
    expect(badge.className).toMatch(/text-info/);
    expect(screen.queryByText(/Heuristico/i)).not.toBeInTheDocument();

    // Bias chip — LONG variant, data-attribute lets the test anchor on
    // semantics rather than colour classes.
    const biasChip = screen.getByTestId("bias-chip");
    expect(biasChip).toHaveAttribute("data-bias", "LONG");
    expect(biasChip.textContent ?? "").toMatch(/LONG/);

    // Probabilities surfaced from the signal.
    expect(screen.getByText(/Bull\s+62%/i)).toBeInTheDocument();
    expect(screen.getByText(/Bear\s+18%/i)).toBeInTheDocument();

    // Horizon and quality readouts present.
    expect(screen.getByText(/Horizonte/i)).toBeInTheDocument();
    expect(screen.getByText(/intraday/i)).toBeInTheDocument();

    // Reason codes block surfaces every code verbatim — provenance is
    // operator-actionable so it must not collapse to a count.
    const reasonBlock = screen.getByTestId("reason-codes");
    expect(reasonBlock.textContent ?? "").toMatch(/regime:RISK_ON/);
    expect(reasonBlock.textContent ?? "").toMatch(/conviction:HIGH/);
  });

  // -------------------------------------------------------------------------
  // Fixture 6 — Bridge signal with SHORT bias from the thesis source. The
  // SHORT variant of the chip must render bear-tinted, distinct from LONG.
  // -------------------------------------------------------------------------
  it("renders SHORT bias chip and Thesis AI badge for thesis-source signal", () => {
    const signal: BingXPredictiveSignal = {
      directional_bias: "SHORT",
      probability_long: 0.25,
      probability_short: 0.65,
      confidence: 0.6,
      horizon: "swing",
      source: "thesis",
      quality_score: 0.6,
      reason_codes: [
        "meta_signal:fetch_failed",
        "predictive_options_2:no_signal",
      ],
    };

    render(<BingxProbabilisticPanel probabilistic={null} signal={signal} />);

    expect(screen.getByText(/Thesis AI/i)).toBeInTheDocument();
    const biasChip = screen.getByTestId("bias-chip");
    expect(biasChip).toHaveAttribute("data-bias", "SHORT");
    expect(biasChip.className).toMatch(/text-bear/);

    // Cascade provenance — both higher-priority failures must be visible.
    const reasonBlock = screen.getByTestId("reason-codes");
    expect(reasonBlock.textContent ?? "").toMatch(/meta_signal:fetch_failed/);
    expect(reasonBlock.textContent ?? "").toMatch(
      /predictive_options_2:no_signal/,
    );
  });

  // -------------------------------------------------------------------------
  // Fixture 7 — Bridge signal takes precedence over the legacy
  // ``probabilistic`` prop when both are supplied. The bridge represents the
  // chosen institutional output; the legacy shape only fills in panels not
  // covered by the bridge cascade.
  // -------------------------------------------------------------------------
  it("prefers bridge signal over legacy probabilistic when both are present", () => {
    const signal: BingXPredictiveSignal = {
      directional_bias: "LONG",
      probability_long: 0.71,
      probability_short: 0.15,
      confidence: 0.8,
      horizon: "intraday",
      source: "meta_signal",
      quality_score: 0.78,
      reason_codes: [],
    };
    const legacy: EquityProbabilistic = {
      ok: true,
      ticker: "GOOGL",
      bull_probability: 0.4, // different number — must NOT appear
      bear_probability: 0.4,
      neutral_probability: 0.2,
      confidence: 0.5,
      source: "equity_heuristic",
    };

    render(<BingxProbabilisticPanel probabilistic={legacy} signal={signal} />);

    // Bridge probabilities win — legacy 40% must not appear, signal 71% must.
    expect(screen.getByText(/Bull\s+71%/i)).toBeInTheDocument();
    expect(screen.queryByText(/Bull\s+40%/i)).not.toBeInTheDocument();
    // Header reflects the bridge source, not the legacy one.
    expect(screen.getByText(/Meta-Signal/i)).toBeInTheDocument();
    expect(screen.queryByText(/Heuristico/i)).not.toBeInTheDocument();
  });
});
