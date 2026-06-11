"use client";
import { DataTable, type Column } from "@/components/panels/DataTable";
import type { PredictiveEngine } from "@/types";
import { categoryColor, confidenceColor, signalColor } from "@/utils/colors";

const CATEGORY_LABEL: Record<string, string> = {
  ML: "ML",
  STATISTICAL: "STAT",
  TECHNICAL: "TECH",
  OPTIONS: "OPT",
  MACRO: "MACRO",
  HYBRID: "HYB",
};

const STATUS_COLOR: Record<string, string> = {
  ACTIVE: "var(--signal-bull)",
  TRAINING: "var(--signal-warn)",
  DEGRADED: "var(--signal-bear)",
};

const COLUMNS: Column<PredictiveEngine>[] = [
  {
    key: "id",
    header: "#",
    width: 32,
    align: "center",
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-muted)",
        }}
      >
        {String(row.id).padStart(2, "0")}
      </span>
    ),
  },
  {
    key: "name",
    header: "Engine",
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-primary)",
        }}
      >
        {row.name}
      </span>
    ),
  },
  {
    key: "category",
    header: "Cat",
    width: 55,
    align: "center",
    render: (row) => {
      const col = categoryColor(row.category);
      return (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9,
            color: col,
            background: `${col}18`,
            border: `1px solid ${col}30`,
            borderRadius: 3,
            padding: "1px 5px",
          }}
        >
          {CATEGORY_LABEL[row.category]}
        </span>
      );
    },
  },
  {
    key: "signal",
    header: "Signal",
    width: 52,
    align: "center",
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: signalColor(row.signal),
        }}
      >
        {row.signal}
      </span>
    ),
  },
  {
    key: "accuracy7d",
    header: "7d Acc",
    width: 55,
    align: "right",
    sortable: true,
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: confidenceColor(row.accuracy7d),
        }}
      >
        {row.accuracy7d}%
      </span>
    ),
  },
  {
    key: "accuracy30d",
    header: "30d Acc",
    width: 58,
    align: "right",
    sortable: true,
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: confidenceColor(row.accuracy30d),
        }}
      >
        {row.accuracy30d}%
      </span>
    ),
  },
  {
    key: "accuracy90d",
    header: "90d Acc",
    width: 58,
    align: "right",
    sortable: true,
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: confidenceColor(row.accuracy90d),
        }}
      >
        {row.accuracy90d}%
      </span>
    ),
  },
  {
    key: "confidence",
    header: "Conf",
    width: 48,
    align: "right",
    sortable: true,
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: confidenceColor(row.confidence),
        }}
      >
        {row.confidence}%
      </span>
    ),
  },
  {
    key: "status",
    header: "Status",
    width: 70,
    align: "center",
    render: (row) => (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          color: STATUS_COLOR[row.status],
          letterSpacing: "0.06em",
        }}
      >
        ● {row.status}
      </span>
    ),
  },
];

interface Props {
  engines: PredictiveEngine[];
}

export function AccuracyLeaderboard({ engines }: Props) {
  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-muted)",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        Accuracy Leaderboard
      </div>

      <DataTable
        columns={COLUMNS}
        data={engines}
        rowKey={(row) => row.id}
        maxHeight={260}
      />
    </div>
  );
}
