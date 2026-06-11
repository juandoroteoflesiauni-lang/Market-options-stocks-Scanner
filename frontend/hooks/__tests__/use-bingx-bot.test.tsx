import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJson } from "@/lib/api-client";

import { useBingxBot, useProductionReadiness } from "../use-bingx-bot";

vi.mock("@/lib/api-client", () => ({
  fetchJson: vi.fn(),
}));

const fetchJsonMock = vi.mocked(fetchJson);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("useBingxBot", () => {
  beforeEach(() => {
    fetchJsonMock.mockReset();
  });

  it("publishes the initial scan without waiting for slow universe discovery", async () => {
    const universe = deferred<{
      universe: Array<{
        symbol: string;
        display_name: string;
        asset_class: "crypto" | "synthetic_stock";
        volume_24h_usdt: number;
        open_interest: number;
        last_price: number;
        max_leverage: number;
        is_tradeable: boolean;
        fmp_symbol: string | null;
        massive_available: boolean;
        venue_symbol: string;
        underlying_symbol: string;
        market_type:
          | "crypto_standard"
          | "stock_perp"
          | "stock_index_perp"
          | "excluded";
        analysis_allowed: boolean;
        execution_allowed: boolean;
        exclusion_reason: string | null;
      }>;
    }>();

    fetchJsonMock.mockImplementation((url) => {
      if (url === "/api/v1/bingx-bot/status") {
        return Promise.resolve({
          dry_run: true,
          universe: ["BTC-USDT"],
          reason_codes: [],
        });
      }
      if (url === "/api/v1/bingx-bot/scan") {
        return Promise.resolve({
          service: "bingx_bot",
          dry_run: true,
          started_at: "2026-05-19T12:00:00Z",
          finished_at: "2026-05-19T12:00:01Z",
          scanner_confirmation: false,
          snapshots: [
            {
              symbol: "BTC-USDT",
              bars: 120,
              latest_close: 77_000,
              volume_z_score: 1.2,
              last_volume: 1000,
              interval: "5m",
              closes_recent: [76_900, 77_000],
            },
          ],
          decisions: [],
        });
      }
      if (url === "/api/v1/bingx-bot/account") {
        return Promise.resolve({
          total_equity_usdt: 10,
          available_margin_usdt: 10,
          used_margin_usdt: 0,
          unrealized_pnl_usdt: 0,
          realized_pnl_today_usdt: 0,
          open_positions: [],
          position_count: 0,
          open_orders: [],
          margin_ratio: null,
          largest_position_pct: null,
          dry_run: true,
          captured_at: "2026-05-19T12:00:01Z",
        });
      }
      if (url === "/api/v1/bingx-bot/universe") {
        return universe.promise;
      }
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });

    const { result, unmount } = renderHook(() => useBingxBot());

    await waitFor(() => {
      expect(result.current.status.snapshots).toHaveLength(1);
    });
    expect(result.current.status.universe).toEqual(["BTC-USDT"]);

    universe.resolve({
      universe: [
        {
          symbol: "AVAX-USDT",
          display_name: "AVAX-USDT",
          asset_class: "crypto",
          volume_24h_usdt: 25_000_000,
          open_interest: 3_000_000,
          last_price: 2100,
          max_leverage: 50,
          is_tradeable: true,
          fmp_symbol: null,
          massive_available: false,
          venue_symbol: "AVAX-USDT",
          underlying_symbol: "AVAX",
          market_type: "crypto_standard",
          analysis_allowed: true,
          execution_allowed: false,
          exclusion_reason: null,
        },
      ],
    });

    await waitFor(() => {
      expect(result.current.status.universe).toEqual(["AVAX-USDT"]);
    });
    expect(result.current.status.universe_details).toHaveLength(1);
    await waitFor(() => {
      const scanCalls = fetchJsonMock.mock.calls.filter(
        ([url]) => url === "/api/v1/bingx-bot/scan",
      );
      expect(scanCalls).toHaveLength(2);
    });
    const scanCalls = fetchJsonMock.mock.calls.filter(
      ([url]) => url === "/api/v1/bingx-bot/scan",
    );
    const dynamicScanOptions = scanCalls[1][1] as { body: string };
    expect(JSON.parse(dynamicScanOptions.body).symbols).toEqual([
      "AAPL-USDT",
      "MSFT-USDT",
      "TSLA-USDT",
      "PLTR-USDT",
      "NVDA-USDT",
      "META-USDT",
      "GOOGL-USDT",
      "BTC-USDT",
      "AVAX-USDT",
    ]);

    unmount();
  });
});

describe("useProductionReadiness", () => {
  beforeEach(() => {
    fetchJsonMock.mockReset();
  });

  it("polls the fast healthcheck by default and exposes a manual deep probe", async () => {
    fetchJsonMock.mockImplementation((url) => {
      if (url === "/api/v1/bingx-bot/healthcheck") {
        return Promise.resolve({ service: "bingx_bot", probe_mode: false });
      }
      if (url === "/api/v1/bingx-bot/healthcheck?probe=true") {
        return Promise.resolve({ service: "bingx_bot", probe_mode: true });
      }
      if (url === "/api/v1/bingx-bot/live-readiness") {
        return Promise.resolve({ ready: false, gates: {}, allowlist: [] });
      }
      if (url === "/api/v1/bingx-bot/cycles?limit=10") {
        return Promise.resolve({ cycles: [], count: 0 });
      }
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });

    const { result, unmount } = renderHook(() => useProductionReadiness());

    await waitFor(() => {
      expect(fetchJsonMock).toHaveBeenCalledWith(
        "/api/v1/bingx-bot/healthcheck",
        expect.objectContaining({ quiet: true }),
      );
    });
    expect(
      fetchJsonMock.mock.calls.some(
        ([url]) => url === "/api/v1/bingx-bot/healthcheck?probe=true",
      ),
    ).toBe(false);

    await result.current.runProbe();

    expect(fetchJsonMock).toHaveBeenCalledWith(
      "/api/v1/bingx-bot/healthcheck?probe=true",
      expect.objectContaining({ quiet: true }),
    );

    unmount();
  });
});
