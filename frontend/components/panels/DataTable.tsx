"use client";
import { useState, useMemo, type ReactNode } from "react";
import { ArrowUp, ArrowDown, ChevronsUpDown } from "lucide-react";

export interface Column<T> {
  key: keyof T | string;
  header: string;
  width?: number | string;
  align?: "left" | "right" | "center";
  sortable?: boolean;
  render?: (row: T, index: number) => ReactNode;
}

interface Props<T extends object> {
  columns: Column<T>[];
  data: T[];
  rowKey: (row: T, i: number) => string | number;
  onRowClick?: (row: T) => void;
  maxHeight?: number | string;
  emptyText?: string;
}

type SortDir = "asc" | "desc" | null;

export function DataTable<T extends object>({
  columns,
  data,
  rowKey,
  onRowClick,
  maxHeight = 400,
  emptyText = "No data",
}: Props<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>(null);

  function handleSort(key: string) {
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir("asc");
    } else if (sortDir === "asc") {
      setSortDir("desc");
    } else {
      setSortKey(null);
      setSortDir(null);
    }
  }

  const sorted = useMemo(() => {
    if (!sortKey || !sortDir) return data;
    return [...data].sort((a, b) => {
      const av = (a as Record<string, unknown>)[sortKey];
      const bv = (b as Record<string, unknown>)[sortKey];
      if (av === bv) return 0;
      const gt = av! > bv! ? 1 : -1;
      return sortDir === "asc" ? gt : -gt;
    });
  }, [data, sortKey, sortDir]);

  return (
    <div
      style={{
        overflowY: "auto",
        maxHeight,
        borderRadius: "var(--radius-md)",
        border: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr
            style={{
              position: "sticky",
              top: 0,
              zIndex: 1,
              background: "var(--bg-elevated)",
            }}
          >
            {columns.map((col) => {
              const key = String(col.key);
              const active = sortKey === key;
              return (
                <th
                  key={key}
                  onClick={
                    col.sortable !== false ? () => handleSort(key) : undefined
                  }
                  style={{
                    padding: "8px 10px",
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    fontWeight: 600,
                    color: active ? "#00C3FF" : "#4A5568",
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    textAlign: col.align ?? "left",
                    width: col.width,
                    cursor: col.sortable !== false ? "pointer" : "default",
                    borderBottom: "1px solid rgba(255,255,255,0.06)",
                    whiteSpace: "nowrap",
                    userSelect: "none",
                    transition: "color 0.15s ease",
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    {col.header}
                    {col.sortable !== false && (
                      <span style={{ opacity: 0.7, display: "flex" }}>
                        {active && sortDir === "asc" ? (
                          <ArrowUp size={10} />
                        ) : active && sortDir === "desc" ? (
                          <ArrowDown size={10} />
                        ) : (
                          <ChevronsUpDown size={10} />
                        )}
                      </span>
                    )}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                style={{
                  padding: 32,
                  textAlign: "center",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "#4A5568",
                }}
              >
                {emptyText}
              </td>
            </tr>
          ) : (
            sorted.map((row, i) => (
              <tr
                key={rowKey(row, i)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={{
                  background:
                    i % 2 === 1 ? "rgba(255,255,255,0.01)" : "transparent",
                  cursor: onRowClick ? "pointer" : "default",
                  transition: "background 0.08s ease",
                  borderBottom: "1px solid rgba(255,255,255,0.03)",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background =
                    "var(--bg-hover)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background =
                    i % 2 === 1 ? "rgba(255,255,255,0.01)" : "transparent";
                }}
              >
                {columns.map((col) => {
                  const key = String(col.key);
                  const val = col.render
                    ? col.render(row, i)
                    : String((row as Record<string, unknown>)[key] ?? "");
                  return (
                    <td
                      key={key}
                      style={{
                        padding: "7px 10px",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "#E8EDF5",
                        textAlign: col.align ?? "left",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {val}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
