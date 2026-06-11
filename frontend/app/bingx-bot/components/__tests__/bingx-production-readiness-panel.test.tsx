import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BingxProductionReadinessPanel } from "@/app/bingx-bot/components/bingx-production-readiness-panel";
import type {
  BingXAuditCycleSummary,
  BingXHealthcheck,
  BingXTelemetry,
  BingXTelemetryGates,
  BingXTelemetryLastProbe,
} from "@/lib/bingx-bot-types";

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

function makeHealthcheck(
  overrides: Partial<BingXHealthcheck> = {},
): BingXHealthcheck {
  return {
    service: "bingx_bot",
    dry_run: true,
    universe_count: 10,
    stock_perp_count: 7,
    stock_index_perp_count: 1,
    crypto_count: 2,
    l2_active_count: 5,
    l2_pending_count: 3,
    options_available_count: 4,
    predictive_available_count: 6,
    execution_allowed_count: 8,
    providers: {
      bingx_api_key: true,
      fmp_api_key: true,
      gemini_api_key: false,
      options_credentials: true,
    },
    probe_mode: false,
    ...overrides,
  };
}

function makeTelemetry(
  overrides: Partial<BingXTelemetry> = {},
): BingXTelemetry {
  const gates: BingXTelemetryGates = {
    enable_live: false,
    client_configured_live: false,
    paper_trading: true,
    vst_mode: false,
    allowlist: [],
    healthcheck: "NEVER_RUN",
    probe_providers: "FAILED",
    audit_persistent: false,
    scheduler_configured: false,
    risk_desk: "UNKNOWN",
    ...(overrides.gates || {}),
  };

  const last_probe: BingXTelemetryLastProbe = {
    probe_ok: false,
    age_s: null,
    fmp_status: null,
    options_status: null,
    l2_active_count: null,
    l2_failed_count: null,
    l2_sample_size: null,
    failures: [],
    ...(overrides.last_probe || {}),
  };

  return {
    captured_at: new Date().toISOString(),
    production_ready: false,
    trading_environment: "vst",
    dry_run: true,
    risk_summary: {
      balance_usdt: 0,
      available_margin_usdt: 0,
      used_margin_usdt: 0,
      unrealized_pnl_usdt: 0,
      realized_pnl_today_usdt: 0,
      open_position_count: 0,
      open_positions: {},
      kill_switch_engaged: false,
      kill_switch_reason: null,
      daily_loss_used_pct: 0,
      policy: {},
    },
    scheduler: {
      configured: false,
      state: "stopped",
      last_cycle_at: null,
      cycle_count: null,
    },
    universe: {
      total_count: 0,
      allowlist: [],
    },
    ...overrides,
    gates,
    last_probe,
  };
}

function makeCycle(
  id: string,
  startedAt: string,
  dryRun = true,
): BingXAuditCycleSummary {
  return {
    cycle_id: id,
    started_at: startedAt,
    finished_at: startedAt,
    dry_run: dryRun,
    universe: ["BTC-USDT"],
    created_at: startedAt,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPanel({
  healthcheck = null,
  telemetry = null,
  cycles = [],
  isLoading = false,
}: Partial<{
  healthcheck: BingXHealthcheck | null;
  telemetry: BingXTelemetry | null;
  cycles: BingXAuditCycleSummary[];
  isLoading: boolean;
}> = {}) {
  return render(
    <BingxProductionReadinessPanel
      healthcheck={healthcheck}
      telemetry={telemetry}
      cycles={cycles}
      isLoading={isLoading}
    />,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("BingxProductionReadinessPanel", () => {
  // ── Rendering shell ───────────────────────────────────────────────────────

  it("renders panel title without crashing when all props are null", () => {
    renderPanel();
    expect(screen.getByText(/Production Readiness/i)).toBeInTheDocument();
  });

  it("shows loading indicator when isLoading=true", () => {
    renderPanel({ isLoading: true });
    expect(screen.getByText(/Actualizando/i)).toBeInTheDocument();
  });

  // ── Live telemetry status ─────────────────────────────────────────────────

  it("shows 'SISTEMA OPERATIVO' badge when ready=true", () => {
    renderPanel({ telemetry: makeTelemetry({ production_ready: true }) });
    expect(screen.getByText(/SISTEMA OPERATIVO/i)).toBeInTheDocument();
  });

  it("shows 'No listo' badge when ready=false", () => {
    renderPanel({ telemetry: makeTelemetry({ production_ready: false }) });
    expect(screen.getByText(/No listo/i)).toBeInTheDocument();
  });

  // ── Gates ─────────────────────────────────────────────────────────────────

  it("renders all gate rows", () => {
    renderPanel({
      telemetry: makeTelemetry({
        gates: {
          enable_live: true,
          client_configured_live: false,
          healthcheck: "NEVER_RUN",
          allowlist: [],
          paper_trading: false,
          audit_persistent: false,
          scheduler_configured: false,
          probe_providers: "FAILED",
          risk_desk: "UNKNOWN",
          vst_mode: false,
        },
      }),
    });
    expect(screen.getByText(/Enable live/i)).toBeInTheDocument();
    expect(screen.getByText(/Client configurado live/i)).toBeInTheDocument();
    expect(screen.getByText(/Healthcheck fresco/i)).toBeInTheDocument();
  });

  // ── Reason codes ──────────────────────────────────────────────────────────

  it("shows ENABLE_LIVE=false reason code when gate fails", () => {
    renderPanel({
      telemetry: makeTelemetry({
        production_ready: false,
        gates: {
          enable_live: false,
          client_configured_live: false,
          healthcheck: "STALE",
          allowlist: [],
          paper_trading: true,
          audit_persistent: false,
          scheduler_configured: false,
          probe_providers: "FAILED",
          risk_desk: "UNKNOWN",
          vst_mode: false,
        },
      }),
    });
    expect(screen.getByText("ENABLE_LIVE=false")).toBeInTheDocument();
    expect(screen.getByText("CLIENT_DRY_RUN")).toBeInTheDocument();
    expect(screen.getByText("HEALTHCHECK_STALE")).toBeInTheDocument();
  });

  it("shows paper trading as a production blocker", () => {
    renderPanel({
      telemetry: makeTelemetry({
        production_ready: false,
        gates: {
          enable_live: true,
          client_configured_live: true,
          healthcheck: "FRESH",
          allowlist: ["BTC-USDT"],
          paper_trading: false,
          audit_persistent: true,
          scheduler_configured: true,
          probe_providers: "OK",
          risk_desk: "OPERATIONAL",
          vst_mode: false,
        },
      }),
    });

    expect(screen.getByText(/Paper trading/i)).toBeInTheDocument();
    expect(screen.getByText(/PAPER_TRADING_ENABLED/i)).toBeInTheDocument();
  });

  it("does not show reason codes when ready=true", () => {
    renderPanel({
      telemetry: makeTelemetry({
        production_ready: true,
        gates: {
          enable_live: true,
          client_configured_live: true,
          healthcheck: "FRESH",
          allowlist: ["BTC-USDT"],
          paper_trading: true,
          audit_persistent: true,
          scheduler_configured: true,
          probe_providers: "OK",
          risk_desk: "OPERATIONAL",
          vst_mode: false,
        },
      }),
    });
    expect(screen.queryByText("ENABLE_LIVE=false")).not.toBeInTheDocument();
    expect(screen.queryByText("CLIENT_DRY_RUN")).not.toBeInTheDocument();
  });

  // ── Allowlist ─────────────────────────────────────────────────────────────

  it("shows a blocking state when allowlist is empty and telemetry is present", () => {
    renderPanel({
      telemetry: makeTelemetry({
        gates: {
          enable_live: false,
          client_configured_live: false,
          paper_trading: true,
          vst_mode: false,
          allowlist: [],
          healthcheck: "NEVER_RUN",
          probe_providers: "FAILED",
          audit_persistent: false,
          scheduler_configured: false,
          risk_desk: "UNKNOWN",
        },
      }),
    });
    expect(screen.getByText(/Allowlist vacia/i)).toBeInTheDocument();
  });

  it("renders each allowlist symbol", () => {
    renderPanel({
      telemetry: makeTelemetry({
        gates: {
          enable_live: false,
          client_configured_live: false,
          paper_trading: true,
          vst_mode: false,
          allowlist: ["BTC-USDT", "AAPL-USDT"],
          healthcheck: "NEVER_RUN",
          probe_providers: "FAILED",
          audit_persistent: false,
          scheduler_configured: false,
          risk_desk: "UNKNOWN",
        },
      }),
    });
    expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    expect(screen.getByText("AAPL-USDT")).toBeInTheDocument();
  });

  // ── Universe counts ───────────────────────────────────────────────────────

  it("renders universe metric cells from healthcheck", () => {
    renderPanel({ healthcheck: makeHealthcheck() });
    // Total count = 10
    expect(screen.getByText("10")).toBeInTheDocument();
    // L2 active count
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("shows provider status from healthcheck", () => {
    renderPanel({
      healthcheck: makeHealthcheck({
        providers: {
          bingx_api_key: true,
          fmp_api_key: false,
          gemini_api_key: false,
          options_credentials: true,
        },
      }),
    });
    // BingX and Options are OK, FMP and Gemini are MISSING
    const oks = screen.getAllByText("OK");
    const missings = screen.getAllByText("MISSING");
    expect(oks.length).toBeGreaterThanOrEqual(2);
    expect(missings.length).toBeGreaterThanOrEqual(2);
  });

  // ── Live probes ───────────────────────────────────────────────────────────

  it("shows FMP probe status when probe_mode=true", () => {
    renderPanel({
      healthcheck: makeHealthcheck({
        probe_mode: true,
        probe_ok: true,
        fmp_probe: {
          status: "ok",
          ticker: "SPY",
          reason: null,
          latency_ms: 120,
        },
        options_probe: {
          status: "skipped",
          ticker: "GOOGL",
          reason: "no_api_key",
          latency_ms: null,
        },
        l2_probe_sample_size: 5,
        l2_probe_active_count: 5,
        l2_probe_failed_count: 0,
        l2_probe_failures: [],
      }),
    });
    expect(screen.getByText(/OK.*120ms/)).toBeInTheDocument();
    expect(screen.getByText(/SKIPPED/i)).toBeInTheDocument();
  });

  it("shows L2 probe failures", () => {
    renderPanel({
      healthcheck: makeHealthcheck({
        probe_mode: true,
        l2_probe_sample_size: 3,
        l2_probe_active_count: 2,
        l2_probe_failed_count: 1,
        l2_probe_failures: [{ symbol: "TSLA-USDT", reason: "timeout" }],
      }),
    });
    expect(screen.getByText(/TSLA-USDT: timeout/)).toBeInTheDocument();
  });

  it("shows 'Todos activos' when L2 probe has no failures", () => {
    renderPanel({
      healthcheck: makeHealthcheck({
        probe_mode: true,
        l2_probe_sample_size: 5,
        l2_probe_active_count: 5,
        l2_probe_failed_count: 0,
        l2_probe_failures: [],
      }),
    });
    expect(screen.getByText(/Todos activos/i)).toBeInTheDocument();
  });

  // ── Cycles ────────────────────────────────────────────────────────────────

  it("shows 'Sin ciclos registrados' when cycles list is empty", () => {
    renderPanel({ cycles: [] });
    expect(screen.getByText(/Sin ciclos registrados/i)).toBeInTheDocument();
  });

  it("renders cycle time and DRY/LIVE label", () => {
    const cycles = [
      makeCycle("c1", "2026-05-21T10:00:00Z", true),
      makeCycle("c2", "2026-05-21T10:05:00Z", false),
    ];
    renderPanel({ cycles });
    expect(screen.getByText("10:00:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("10:05:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("DRY")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("shows at most 6 cycles even when more are provided", () => {
    const cycles = Array.from({ length: 10 }, (_, i) =>
      makeCycle(`c${i}`, `2026-05-21T${String(i).padStart(2, "0")}:00:00Z`),
    );
    renderPanel({ cycles });
    // 6 DRY labels visible (not 10)
    const dryLabels = screen.getAllByText("DRY");
    expect(dryLabels.length).toBe(6);
  });

  // ── HCGate TTL detail ─────────────────────────────────────────────────────

  it("shows TTL detail in healthcheck gate row when age is known", () => {
    renderPanel({
      telemetry: makeTelemetry({
        last_probe: {
          probe_ok: false,
          age_s: 400,
          failures: [],
          fmp_status: null,
          options_status: null,
          l2_active_count: null,
          l2_failed_count: null,
          l2_sample_size: null,
        },
        gates: {
          enable_live: false,
          client_configured_live: false,
          paper_trading: true,
          vst_mode: false,
          allowlist: [],
          healthcheck: "NEVER_RUN",
          probe_providers: "FAILED",
          audit_persistent: false,
          scheduler_configured: false,
          risk_desk: "UNKNOWN",
        },
      }),
    });
    expect(screen.getByText(/400s/)).toBeInTheDocument();
  });

  it("shows 'Nunca ejecutado' when healthcheck has never run", () => {
    renderPanel({
      telemetry: makeTelemetry({
        gates: {
          enable_live: false,
          client_configured_live: false,
          paper_trading: true,
          vst_mode: false,
          allowlist: [],
          healthcheck: "NEVER_RUN",
          probe_providers: "FAILED",
          audit_persistent: false,
          scheduler_configured: false,
          risk_desk: "UNKNOWN",
        },
      }),
    });
    expect(screen.getByText(/Nunca ejecutado/i)).toBeInTheDocument();
  });
});
