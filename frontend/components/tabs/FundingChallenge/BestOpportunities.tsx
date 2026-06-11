"use client";
import { DataTable, type Column } from "@/components/panels/DataTable";
import { PhaseTag } from "@/components/panels/PhaseTag";
import { TickerLogo } from "@/components/panels/TickerLogo";
import { BEST_SETUPS, type BestSetup } from "@/data/funding";

export function BestOpportunities() {
  const columns: Column<BestSetup>[] = [
    {
      key: "symbol",
      header: "Symbol",
      width: 96,
      render: (row) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <TickerLogo symbol={row.symbol} size={16} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 700,
              color: "#00C3FF",
            }}
          >
            {row.symbol}
          </span>
        </span>
      ),
    },
    {
      key: "phase",
      header: "Phase",
      width: 60,
      align: "center",
      render: (row) => <PhaseTag phase={row.phase} />,
    },
    {
      key: "direction",
      header: "Dir",
      width: 55,
      align: "center",
      render: (row) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            fontWeight: 600,
            color: row.direction === "LONG" ? "#00E676" : "#FF3D5A",
            padding: "2px 6px",
            background: row.direction === "LONG" ? "#00E67615" : "#FF3D5A15",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {row.direction}
        </span>
      ),
    },
    {
      key: "score",
      header: "Score",
      width: 60,
      align: "right",
      render: (row) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            fontWeight: 700,
            color: "#00E676",
          }}
        >
          {row.score.toFixed(1)}
        </span>
      ),
    },
    {
      key: "confidence",
      header: "Conf%",
      width: 55,
      align: "right",
      render: (row) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#E8EDF5",
          }}
        >
          {row.confidence}%
        </span>
      ),
    },
    {
      key: "compliance",
      header: "Rule%",
      width: 55,
      align: "right",
      render: (row) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color:
              row.compliance >= 90
                ? "#00E676"
                : row.compliance >= 70
                  ? "#FFB800"
                  : "#FF3D5A",
          }}
        >
          {row.compliance}%
        </span>
      ),
    },
    {
      key: "rr",
      header: "R:R",
      width: 50,
      align: "right",
      render: (row) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#8B9AAF",
          }}
        >
          {row.rr.toFixed(2)}
        </span>
      ),
    },
    {
      key: "strategy",
      header: "Strategy",
      render: (row) => (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 180,
            display: "block",
          }}
          title={row.strategy}
        >
          {row.strategy}
        </span>
      ),
    },
  ];

  return (
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
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
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
          Best Compliant Setups
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#00E676",
            padding: "2px 8px",
            background: "#00E67615",
            borderRadius: "var(--radius-sm)",
            border: "1px solid #00E67630",
          }}
        >
          Score = Conf × Rule / 100
        </span>
      </div>

      <DataTable
        columns={columns}
        data={BEST_SETUPS}
        rowKey={(row) => row.id}
        maxHeight={260}
      />

      {/* Selected setup note */}
      <div
        style={{
          background: "var(--bg-elevated)",
          border: "1px solid rgba(0,230,118,0.1)",
          borderRadius: "var(--radius-md)",
          padding: "8px 10px",
          display: "flex",
          flexDirection: "column",
          gap: 3,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: "#4A5568",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
          }}
        >
          Top Setup Note
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#8B9AAF",
          }}
        >
          {BEST_SETUPS[0]?.note}
        </span>
      </div>
    </div>
  );
}
