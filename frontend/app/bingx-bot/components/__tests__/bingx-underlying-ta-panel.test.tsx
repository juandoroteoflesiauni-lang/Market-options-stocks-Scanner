/**
 * Render tests for `BingxTaTabs` — the tabbed wrapper around venue-TA and
 * underlying-TA panels used in the BingX analysis drawer.
 *
 * Survival contract:
 *   - Crypto path (no underlying signal, no error): MUST collapse to the
 *     legacy single-panel UI. Showing tabs here would imply evidence we
 *     don't have.
 *   - stock_perp path with underlying_ta available: MUST surface BOTH tabs
 *     so the operator can compare venue vs underlying — they are different
 *     markets and treating them as one would hide divergence.
 *   - underlying_ta with ok=false: the tab must still exist, but content
 *     must explicitly render the unavailable reason so a degraded engine
 *     never masquerades as a working one. Venue tab must remain functional.
 *
 * We touch only fixture data. Interactions are driven through @testing-
 * library/user-event so the tab toggle is exercised as a real click.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BingxTaTabs } from "@/app/bingx-bot/components/bingx-underlying-ta-panel";
import type { BingXTAMetrics, UnderlyingTA } from "@/lib/bingx-bot-types";

// ---------------------------------------------------------------------------
// Fixture: a minimal but valid venue-TA metrics object. The legacy panel
// renders RSI, VSA, VWAP and EMA stack — leaving these as null is fine in
// the test, the panel only formats and never crashes on nulls.
// ---------------------------------------------------------------------------
function makeVenueTa(): BingXTAMetrics {
  return {
    rsi_14: 55,
    ema_9: 100,
    ema_21: 98,
    ema_50: 95,
    vwap: 99,
    vwap_upper_1: 100,
    vwap_lower_1: 98,
    vsa_delta: 1.2,
    vsa_z_score: 0.8,
    trend: "bullish",
  };
}

describe("BingxTaTabs", () => {
  // -------------------------------------------------------------------------
  // Fixture 1 — crypto-only path. No underlying signal, no error reason.
  // The wrapper must render the legacy `BingxTaPanel` directly (header
  // literal: "Capa tecnica") and MUST NOT render any tab buttons, because
  // tabs would falsely imply that underlying data is on the way.
  // -------------------------------------------------------------------------
  it("renders only legacy venue panel when no underlying data is present", () => {
    render(<BingxTaTabs venueTa={makeVenueTa()} underlyingTa={null} />);

    // Legacy panel header.
    expect(screen.getByText(/Capa tecnica/i)).toBeInTheDocument();

    // No tab buttons should appear in this branch.
    expect(
      screen.queryByRole("button", { name: /^Venue TA$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Underlying TA$/i }),
    ).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Fixture 2 — stock_perp with underlying_ta available. Both tabs must
  // render. Switching to the underlying tab must surface the underlying
  // ticker and the bullish trend badge — the two operator-facing facts that
  // confirm the underlying engine is wired in and emitting fresh data.
  // -------------------------------------------------------------------------
  it("renders Venue/Underlying tabs and switches when stock_perp underlying is available", async () => {
    const user = userEvent.setup();
    const underlyingTa: UnderlyingTA = {
      ok: true,
      ticker: "GOOGL",
      rsi_14: 58.3,
      ema_fast: 175.3,
      ema_slow: 172.1,
      trend_direction: "bullish",
      source: "fmp",
      bars_used: 100,
    };

    render(
      <BingxTaTabs
        venueTa={makeVenueTa()}
        underlyingTa={underlyingTa}
        underlyingSymbol="GOOGL"
      />,
    );

    // Both tab buttons must render.
    const venueTab = screen.getByRole("button", { name: /^Venue TA$/i });
    const underlyingTab = screen.getByRole("button", {
      name: /^Underlying TA$/i,
    });
    expect(venueTab).toBeInTheDocument();
    expect(underlyingTab).toBeInTheDocument();

    // Default tab is "venue" → legacy panel visible.
    expect(screen.getByText(/Capa tecnica/i)).toBeInTheDocument();

    // Switch to the underlying tab.
    await user.click(underlyingTab);

    // Underlying header carries the bracketed symbol [GOOGL]. We anchor on
    // the symbol literal — operators rely on it to spot mis-routing.
    expect(screen.getByText(/\[GOOGL\]/)).toBeInTheDocument();

    // Trend badge for bullish → literal "Alcista".
    expect(screen.getByText(/Alcista/i)).toBeInTheDocument();

    // Legacy panel must no longer be in the DOM (tab is exclusive).
    expect(screen.queryByText(/Capa tecnica/i)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Fixture 3 — underlying_ta with ok=false. The tab must exist (because
  // the routing tells us underlying SHOULD be present) but the panel must
  // render an unavailable block with the operator-readable reason. The
  // venue tab must remain functional — degradation of underlying is not a
  // reason to lose access to venue TA.
  // -------------------------------------------------------------------------
  it("renders unavailable block on underlying tab and preserves venue tab", async () => {
    const user = userEvent.setup();
    const underlyingTa: UnderlyingTA = {
      ok: false,
      reason: "no_equity_data_source",
    };
    const errorReason = "UNAVAILABLE: no_equity_data_source";

    render(
      <BingxTaTabs
        venueTa={makeVenueTa()}
        underlyingTa={underlyingTa}
        errorReason={errorReason}
      />,
    );

    // Both tab buttons still render (the underlying tab existing is the
    // operator's signal that this is a stock_perp path).
    const venueTab = screen.getByRole("button", { name: /^Venue TA$/i });
    const underlyingTab = screen.getByRole("button", {
      name: /^Underlying TA$/i,
    });
    expect(venueTab).toBeInTheDocument();
    expect(underlyingTab).toBeInTheDocument();

    // Switch to the underlying tab — must show the unavailable block.
    await user.click(underlyingTab);

    // Unavailable block heading is the literal "Underlying TA no disponible".
    expect(
      screen.getByText(/Underlying TA no disponible/i),
    ).toBeInTheDocument();

    // The error reason passed via `errorReason` takes precedence and must
    // surface verbatim so the operator can correlate with backend logs.
    expect(
      screen.getByText(/UNAVAILABLE: no_equity_data_source/),
    ).toBeInTheDocument();

    // Trend badge in the header must show "N/A" (not Alcista/Bajista/
    // Neutral, which would imply we have a directional read).
    expect(screen.getByText(/^N\/A$/i)).toBeInTheDocument();

    // Switch back to venue — the legacy panel must reappear, intact.
    await user.click(venueTab);
    expect(screen.getByText(/Capa tecnica/i)).toBeInTheDocument();

    // And the unavailable block must no longer be on screen.
    expect(
      screen.queryByText(/Underlying TA no disponible/i),
    ).not.toBeInTheDocument();
  });
});
