/**
 * Composition tests for `BingxAnalysisDrawer`.
 *
 * Survival contract enforced here — independent of the per-panel contracts
 * already covered by the underlying-ta and probabilistic panel tests:
 *
 *   - The drawer is a *composition* point. When the backend returns partial
 *     errors, each panel MUST degrade in isolation: a failure in one engine
 *     never poisons the others. A degraded probabilistic engine cannot dim
 *     a working venue TA; a missing underlying TA cannot inject error
 *     strings into the probabilistic distribution.
 *
 *   - When the upstream payload is absent (loading or transient null), the
 *     drawer must render its shell without crashing — no `undefined is not
 *     a function`, no React error boundary tripping.
 *
 *   - Crypto-only routing (no underlying, no equity options) must collapse
 *     to the legacy single-panel layout — showing tabs or probabilistic
 *     distribution there would imply evidence we don't have.
 *
 * Mocking strategy:
 *   - `useBingxAnalysis` is the hook the drawer calls to fetch its payload.
 *     We mock it so each test can inject a fixture deterministically — no
 *     real fetch, no timers.
 *   - `BingxChart` dynamically imports `lightweight-charts` and observes
 *     resize via `ResizeObserver`, neither of which is meaningful under
 *     jsdom. We stub it to a passive placeholder so composition assertions
 *     remain stable across chart library upgrades.
 *
 * We only feed fixture data — the drawer and its real subcomponents render
 * unmodified, which is what makes this a true integration check at the
 * component-composition tier.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BingxAnalysisDrawer } from "@/app/bingx-bot/components/bingx-analysis-drawer";
import type {
  BingXAnalysisResponse,
  BingXSnapshotSummary,
  BingXTAMetrics,
  EquityProbabilistic,
  UnderlyingTA,
} from "@/lib/bingx-bot-types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
//
// `useBingxAnalysis` is the only IO seam in the drawer. Each test sets the
// return value before render. Returning a stable object keeps React from
// re-rendering between assertions and from triggering the polling interval
// (the hook is the only thing that sets up `setInterval`).
vi.mock("@/hooks/use-bingx-analysis", () => ({
  useBingxAnalysis: vi.fn(),
}));

// `BingxChart` pulls in `lightweight-charts` via dynamic import and wires a
// `ResizeObserver`. Under jsdom neither is available, and even if we shimmed
// them the chart is purely visual — composition assertions don't need its
// real output. We stub it so the drawer mounts cleanly.
vi.mock("@/app/bingx-bot/components/bingx-chart", () => ({
  BingxChart: () => <div data-testid="bingx-chart-stub" />,
}));

import { useBingxAnalysis } from "@/hooks/use-bingx-analysis";

const useBingxAnalysisMock = vi.mocked(useBingxAnalysis);

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------
//
// These mirror the real backend response shape from `/analysis/{symbol}`.
// Keeping them as small helpers (vs. inline literals per test) makes the
// per-scenario diffs obvious — each `it` only overrides what matters.

function makeVenueTa(): BingXTAMetrics {
  return {
    rsi_14: 55,
    ema_9: 175,
    ema_21: 172,
    ema_50: 170,
    vwap: 174,
    vwap_upper_1: 176,
    vwap_lower_1: 172,
    vsa_delta: 1.2,
    vsa_z_score: 0.8,
    trend: "bullish",
  };
}

function makeStockPerpAnalysis(
  overrides: Partial<BingXAnalysisResponse> = {},
): BingXAnalysisResponse {
  const venueTa = makeVenueTa();
  return {
    symbol: "GOOGL-USDT",
    interval: "5m",
    klines: [],
    ta: venueTa,
    venue_ta: venueTa,
    options: null,
    venue_symbol: "GOOGL-USDT",
    underlying_symbol: "GOOGL",
    market_type: "stock_perp",
    underlying_ta: null,
    probabilistic: null,
    data_sources: ["venue_klines"],
    errors: {},
    ...overrides,
  };
}

function makeCryptoAnalysis(): BingXAnalysisResponse {
  const venueTa = makeVenueTa();
  return {
    symbol: "BTC-USDT",
    interval: "5m",
    klines: [],
    ta: venueTa,
    venue_ta: venueTa,
    options: null,
    venue_symbol: "BTC-USDT",
    underlying_symbol: "BTC",
    market_type: "crypto_standard",
    underlying_ta: null,
    probabilistic: null,
    data_sources: ["venue_klines"],
    errors: {},
  };
}

function makeSnapshot(symbol: string): BingXSnapshotSummary {
  return {
    symbol,
    bars: 120,
    latest_close: 175.42,
    volume_z_score: 1.3,
    last_volume: 1_234_567,
    interval: "5m",
    closes_recent: [],
  };
}

// Set the hook return value with the canonical loading/error defaults so
// each test only spells out the analysis payload.
function setAnalysis(
  analysis: BingXAnalysisResponse | null,
  isLoading = false,
) {
  useBingxAnalysisMock.mockReturnValue({
    analysis,
    isLoading,
    error: null,
  });
}

describe("BingxAnalysisDrawer composition", () => {
  beforeEach(() => {
    useBingxAnalysisMock.mockReset();
  });

  // -------------------------------------------------------------------------
  // Scenario 1 — analysis=null. The drawer is open (symbol provided) and
  // the hook reports loading. The shell must mount without crashing, and
  // no per-panel "data" literal (probability, trend, RSI gauge value) may
  // leak. This guards against a regression where a panel reads a field of
  // `analysis?.xyz` without the `?.` and explodes on null.
  // -------------------------------------------------------------------------
  it("renders shell without crashing when analysis is null (loading)", () => {
    setAnalysis(null, /* isLoading */ true);

    render(
      <BingxAnalysisDrawer
        symbol="GOOGL-USDT"
        snapshot={makeSnapshot("GOOGL-USDT")}
        onClose={() => {}}
      />,
    );

    // Shell elements that depend only on props: the symbol in the header
    // and the "Updating" status line driven by isLoading.
    expect(screen.getByText(/GOOGL-USDT/)).toBeInTheDocument();
    expect(screen.getByText(/Actualizando stack tecnico/i)).toBeInTheDocument();

    // Chart stub mounted — drawer wired chart subtree without throwing.
    expect(screen.getByTestId("bingx-chart-stub")).toBeInTheDocument();

    // No probability distribution leaked (probabilistic is null + loading).
    expect(screen.queryByText(/Bull\s+\d+%/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Bear\s+\d+%/i)).not.toBeInTheDocument();

    // No trend badge (`Alcista` / `Bajista` / `Neutral`) — venue TA is in
    // its loading state, so the panel renders the pulse placeholder.
    expect(screen.queryByText(/^Alcista$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Bajista$/i)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Scenario 2 — stock_perp with BOTH engines failing. Venue TA is the
  // only working signal. Critical asserts:
  //   - Venue panel renders intact (RSI value + trend badge visible).
  //   - Probabilistic panel surfaces "No disponible" with its OWN reason.
  //   - Underlying TA panel surfaces "No disponible" with its OWN reason.
  //   - The probabilistic reason string must NOT appear inside venue TA
  //     (and vice-versa) — degradation is per-engine, not contagious.
  // -------------------------------------------------------------------------
  it("degrades probabilistic and underlying TA independently while venue TA stays intact", async () => {
    const user = userEvent.setup();
    const analysis = makeStockPerpAnalysis({
      errors: {
        underlying_ta: "UNAVAILABLE: no_equity_data_source",
        probabilistic: "UNAVAILABLE: low_data_quality",
      },
    });
    setAnalysis(analysis);

    const { container } = render(
      <BingxAnalysisDrawer
        symbol="GOOGL-USDT"
        snapshot={makeSnapshot("GOOGL-USDT")}
        onClose={() => {}}
      />,
    );

    // ── Venue TA is intact (default tab) ─────────────────────────────────
    // The legacy panel header literal is "Capa tecnica" and the bullish
    // trend renders as "Alcista". RSI 14 = 55 → "55.0". Tabs render
    // because the underlying TA branch was attempted (errorReason set),
    // but the default selection is the venue tab.
    expect(screen.getByText(/Capa tecnica/i)).toBeInTheDocument();
    expect(screen.getByText(/^Alcista$/i)).toBeInTheDocument();
    expect(screen.getByText(/^55\.0$/)).toBeInTheDocument();

    // ── Probabilistic panel: explicit "No disponible" with its reason ─
    // The probabilistic panel scopes "No disponible" to a div (header lives
    // in a different element), matching the pattern used in the unit test
    // for that panel. It renders alongside venue TA, never gated by a tab.
    const probUnavailable = screen.getAllByText(/^No disponible$/i, {
      selector: "div",
    });
    expect(probUnavailable.length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText(/UNAVAILABLE: low_data_quality/),
    ).toBeInTheDocument();

    // ── Cross-contamination guard (venue tab visible) ───────────────────
    // While the venue panel is mounted, neither error string may appear
    // inside its section. This is the strongest form of the "degradation
    // does not leak" check, because both reasons exist in the payload.
    const venueHeader = screen.getByText(/Capa tecnica/i);
    const venueSection = venueHeader.closest("section");
    expect(venueSection).not.toBeNull();
    expect(venueSection!.textContent ?? "").not.toMatch(
      /UNAVAILABLE: low_data_quality/,
    );
    expect(venueSection!.textContent ?? "").not.toMatch(
      /UNAVAILABLE: no_equity_data_source/,
    );

    // ── Switch to the Underlying TA tab ─────────────────────────────────
    // The tab buttons must exist (errorReason on underlying triggers the
    // tabbed layout). Clicking surfaces the underlying engine's own
    // unavailable block with its own reason — proving the failure surface
    // belongs to that panel, not to venue or probabilistic.
    const underlyingTab = screen.getByRole("button", {
      name: /^Underlying TA$/i,
    });
    await user.click(underlyingTab);

    expect(
      screen.getByText(/Underlying TA no disponible/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/UNAVAILABLE: no_equity_data_source/),
    ).toBeInTheDocument();

    // Probabilistic reason MUST NOT leak into the underlying panel block.
    const underlyingHeader = screen.getByText(/Underlying TA no disponible/i);
    const underlyingSection = underlyingHeader.closest("section");
    expect(underlyingSection).not.toBeNull();
    expect(underlyingSection!.textContent ?? "").not.toMatch(
      /UNAVAILABLE: low_data_quality/,
    );

    // Sanity: chart subtree wired without throwing.
    expect(
      container.querySelector("[data-testid='bingx-chart-stub']"),
    ).not.toBeNull();
  });

  // -------------------------------------------------------------------------
  // Scenario 3 — stock_perp with probabilistic OK, underlying TA failing.
  // The asymmetric case: the probabilistic distribution must render with
  // its real numbers, and the underlying engine must surface its own
  // failure reason. This guards the contract that a failing TA does NOT
  // dim a working probabilistic readout — they are sourced independently.
  // -------------------------------------------------------------------------
  it("renders probabilistic distribution while underlying TA shows unavailable", async () => {
    const user = userEvent.setup();
    const probabilistic: EquityProbabilistic = {
      ok: true,
      ticker: "GOOGL",
      bull_probability: 0.62,
      bear_probability: 0.24,
      neutral_probability: 0.14,
      confidence: 0.71,
      source: "equity_heuristic",
      features: {
        rsi_14: 58,
        momentum_10: 2.8,
        return_zscore_20: 0.9,
        atr_norm_14: 1.4,
        coverage: 1.0,
      },
    };
    const analysis = makeStockPerpAnalysis({
      probabilistic,
      errors: { underlying_ta: "UNAVAILABLE: no_equity_data_source" },
    });
    setAnalysis(analysis);

    render(
      <BingxAnalysisDrawer
        symbol="GOOGL-USDT"
        snapshot={makeSnapshot("GOOGL-USDT")}
        onClose={() => {}}
      />,
    );

    // ── Probabilistic OK: distribution and source badge render ──────────
    // The "Bull 62%" footer literal is the operator-facing readout.
    expect(screen.getByText(/Bull\s+62%/i)).toBeInTheDocument();
    // Source badge for equity_heuristic surfaces the "Heuristico" literal.
    expect(screen.getByText(/Heuristico/i)).toBeInTheDocument();
    // Confidence renders as 71% (0.71 rounded).
    expect(screen.getByText(/^71%$/)).toBeInTheDocument();

    // ── Switch to underlying tab to reveal its failure state ───────────
    // The tab exists because errorReason is set (routing detected stock_perp).
    // `BingxTaTabs` uses conditional rendering — the underlying panel content
    // is only in the DOM when that tab is active.
    const underlyingTab = screen.getByRole("button", {
      name: /^Underlying TA$/i,
    });
    await user.click(underlyingTab);

    // ── Underlying TA: unavailable block surfaces with its OWN reason ──
    expect(
      screen.getByText(/Underlying TA no disponible/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/UNAVAILABLE: no_equity_data_source/),
    ).toBeInTheDocument();

    // ── Probabilistic panel persists after tab switch ───────────────────
    // The probabilistic panel is in a separate section below the TA tabs;
    // switching TA tabs must not affect it.
    expect(screen.getByText(/Bull\s+62%/i)).toBeInTheDocument();

    // ── Contamination guard ─────────────────────────────────────────────
    // BingxProbabilisticPanel wraps its content in its own <section>.
    // Scope to it and verify the underlying-TA error string never bleeds in.
    const probHeader = screen.getByText(/Senales probabilisticas/i);
    const probSection = probHeader.closest("section");
    expect(probSection).not.toBeNull();
    expect(probSection!.textContent ?? "").not.toMatch(
      /UNAVAILABLE: no_equity_data_source/,
    );
    // The operator-facing "Bull 62%" line must live inside the probabilistic
    // section (not somewhere else by accident).
    expect(probSection!.textContent ?? "").toMatch(/Bull\s+62%/);
  });

  // -------------------------------------------------------------------------
  // Scenario 4 — crypto path. No underlying, no equity options, no errors.
  // The drawer must collapse to its simplest form:
  //   - No Venue/Underlying tab buttons (would imply absent evidence).
  //   - No probabilistic distribution (the equity engine never ran).
  //   - Render must succeed; the chart stub must mount.
  // -------------------------------------------------------------------------
  it("collapses to single-panel layout with no probabilistic distribution for crypto", () => {
    setAnalysis(makeCryptoAnalysis());

    render(
      <BingxAnalysisDrawer
        symbol="BTC-USDT"
        snapshot={makeSnapshot("BTC-USDT")}
        onClose={() => {}}
      />,
    );

    // Legacy single panel is up.
    expect(screen.getByText(/Capa tecnica/i)).toBeInTheDocument();

    // No tab buttons — `BingxTaTabs` only renders tabs when underlying or
    // an underlying error is present. Crypto has neither.
    expect(
      screen.queryByRole("button", { name: /^Venue TA$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Underlying TA$/i }),
    ).not.toBeInTheDocument();

    // Probabilistic section is mounted (the drawer always renders it) but
    // because `probabilistic` is null the distribution MUST NOT appear.
    // The operator sees "No disponible", not a confident bull/bear split.
    expect(screen.queryByText(/Bull\s+\d+%/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Bear\s+\d+%/i)).not.toBeInTheDocument();

    // Chart subtree wired.
    expect(screen.getByTestId("bingx-chart-stub")).toBeInTheDocument();

    // Quiet sanity: header still shows the symbol (proves shell rendered).
    expect(screen.getByText(/BTC-USDT/)).toBeInTheDocument();

    // Underscore-style placeholder: `UnderlyingTA` type satisfied for
    // future maintainers reading this file — referenced once so an
    // unused-import lint can't drop it from the bundle.
    const _ta: UnderlyingTA | null = null;
    expect(_ta).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Scenario 5 — L2 active. Stock perp with backend lob_status="active" and
  // a quality score. The chip MUST render the "active" variant and surface
  // the quality percentage so operators can see depth confidence at a glance.
  // -------------------------------------------------------------------------
  it("renders L2 active chip with quality score when lob_status is active", () => {
    setAnalysis(
      makeStockPerpAnalysis({
        lob_status: "active",
        lob_quality_score: 0.82,
        lob_analysis: {
          ok: true,
          source: "bingx_l2_snapshot_rest",
          data_quality_score: 0.82,
        },
        data_sources: ["venue_klines", "bingx_l2_snapshot_rest"],
      }),
    );

    render(
      <BingxAnalysisDrawer
        symbol="GOOGL-USDT"
        snapshot={makeSnapshot("GOOGL-USDT")}
        onClose={() => {}}
      />,
    );

    const chip = screen.getByTestId("l2-status-chip");
    expect(chip).toHaveAttribute("data-status", "active");
    expect(chip.textContent ?? "").toMatch(/L2 Activo/);
    // Quality score rendered as integer percentage.
    expect(chip.textContent ?? "").toMatch(/82%/);
  });

  // -------------------------------------------------------------------------
  // Scenario 6 — L2 pending. Backend explicitly returned an unavailable
  // LOBDynamicsAnalysis (ok=False) for an equity perp. Status chip shows
  // "pending" — distinct from both "active" (real depth wired) and
  // "unavailable" (no L2 pipeline at all). The quality label must be
  // suppressed because there is no trustworthy score to show.
  // -------------------------------------------------------------------------
  it("renders L2 pending chip without quality score when lob_status is pending", () => {
    setAnalysis(
      makeStockPerpAnalysis({
        lob_status: "pending",
        lob_quality_score: null,
        lob_analysis: {
          ok: false,
          source: "bingx_l2_unavailable",
          error: "l2_unavailable:snapshot_empty",
        },
        errors: { l2: "UNAVAILABLE: l2_unavailable:snapshot_empty" },
      }),
    );

    render(
      <BingxAnalysisDrawer
        symbol="GOOGL-USDT"
        snapshot={makeSnapshot("GOOGL-USDT")}
        onClose={() => {}}
      />,
    );

    const chip = screen.getByTestId("l2-status-chip");
    expect(chip).toHaveAttribute("data-status", "pending");
    expect(chip.textContent ?? "").toMatch(/L2 Pendiente/);
    // No percentage when quality score is missing.
    expect(chip.textContent ?? "").not.toMatch(/%/);
  });

  // -------------------------------------------------------------------------
  // Scenario 7 — L2 unavailable. Backend signaled lob_status="unavailable"
  // (e.g. crypto, or service raised). Chip surfaces a distinct "N/D" state
  // so operators don't conflate it with a pending equity fetch.
  // -------------------------------------------------------------------------
  it("renders L2 unavailable chip when lob_status is unavailable", () => {
    setAnalysis(
      makeStockPerpAnalysis({
        lob_status: "unavailable",
        lob_quality_score: null,
        lob_analysis: null,
        errors: { l2: "UNAVAILABLE: l2_fetch_failed" },
      }),
    );

    render(
      <BingxAnalysisDrawer
        symbol="GOOGL-USDT"
        snapshot={makeSnapshot("GOOGL-USDT")}
        onClose={() => {}}
      />,
    );

    const chip = screen.getByTestId("l2-status-chip");
    expect(chip).toHaveAttribute("data-status", "unavailable");
    expect(chip.textContent ?? "").toMatch(/L2 N\/D/);
  });
});
