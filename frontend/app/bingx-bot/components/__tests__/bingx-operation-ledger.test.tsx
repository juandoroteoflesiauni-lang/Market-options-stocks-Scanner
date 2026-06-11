import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BingxOperationLedger } from "@/app/bingx-bot/components/bingx-operation-ledger";
import type { BingXOperationLedgerRow } from "@/lib/bingx-bot-types";

function makeOperation(
  overrides: Partial<BingXOperationLedgerRow> = {},
): BingXOperationLedgerRow {
  return {
    operation_id: "cycle-a:BTC-USDT:execution:0",
    cycle_id: "cycle-a",
    event_type: "execution",
    started_at: "2026-05-21T00:02:00Z",
    finished_at: "2026-05-21T00:03:00Z",
    dry_run: true,
    symbol: "BTC-USDT",
    side: "BUY",
    suitability: "ALLOW",
    probability: 0.72,
    authorized: true,
    execution_ok: true,
    order_type: "MARKET",
    quantity: 0.001,
    notional_usdt: 25,
    reference_price: 50_000,
    venue_order_id: "dry_BTC",
    client_order_id: null,
    realized_pnl_usdt: 1.25,
    pnl_pct: 5,
    reason_codes: ["VSA_SPIKE", "RISK_OK"],
    error: null,
    ...overrides,
  };
}

describe("BingxOperationLedger", () => {
  it("renders operation rows with pnl percent and trading reasons", () => {
    render(<BingxOperationLedger operations={[makeOperation()]} />);

    expect(screen.getByText(/Bitacora de operaciones/i)).toBeInTheDocument();
    expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    expect(screen.getByText("+$1.25")).toBeInTheDocument();
    expect(screen.getByText("+5.00%")).toBeInTheDocument();
    expect(screen.getByText(/VSA_SPIKE \/ RISK_OK/i)).toBeInTheDocument();
  });

  it("shows blocked decisions without fabricated pnl", () => {
    render(
      <BingxOperationLedger
        operations={[
          makeOperation({
            operation_id: "cycle-b:SOL-USDT:decision:0",
            event_type: "decision",
            symbol: "SOL-USDT",
            side: null,
            suitability: "BLOCK",
            authorized: false,
            execution_ok: null,
            notional_usdt: null,
            realized_pnl_usdt: null,
            pnl_pct: null,
            reason_codes: ["NO_VOLUME_SPIKE"],
          }),
        ]}
      />,
    );

    expect(screen.getByText("SOL-USDT")).toBeInTheDocument();
    expect(screen.getByText("BLOCK")).toBeInTheDocument();
    expect(screen.getAllByText("--").length).toBeGreaterThan(0);
    expect(screen.getByText("NO_VOLUME_SPIKE")).toBeInTheDocument();
  });
});
