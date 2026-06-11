"use client";

import * as React from "react";
import { Crosshair, DatabaseZap } from "lucide-react";

import type {
  BingXInstrument,
  BingXSnapshotSummary,
} from "@/lib/bingx-bot-types";
import { EmptyState, TerminalPanel } from "@/components/ui/terminal";
import { cn } from "@/lib/utils";

import { BingxAssetCard } from "./bingx-asset-card";

interface BingxAssetMonitorProps {
  snapshots: BingXSnapshotSummary[];
  universe: string[];
  universeDetails: BingXInstrument[];
  selectedSymbol: string | null;
  drawerOpen: boolean;
  onSelectSymbol: (sym: string) => void;
}

export function BingxAssetMonitor({
  snapshots,
  universe,
  universeDetails,
  selectedSymbol,
  drawerOpen,
  onSelectSymbol,
}: BingxAssetMonitorProps) {
  const snapBySymbol = React.useMemo(() => {
    const m = new Map<string, BingXSnapshotSummary>();
    for (const s of snapshots) m.set(s.symbol, s);
    return m;
  }, [snapshots]);

  const symbols = universe.length ? universe : snapshots.map((s) => s.symbol);
  const instrumentBySymbol = React.useMemo(() => {
    const m = new Map<string, BingXInstrument>();
    for (const item of universeDetails) m.set(item.symbol, item);
    return m;
  }, [universeDetails]);

  return (
    <TerminalPanel
      title="Asset Monitor"
      eyebrow={
        <span className="inline-flex items-center gap-1.5">
          <Crosshair className="h-3.5 w-3.5 text-info" />
          Tape de simbolos
        </span>
      }
      source={
        <span className="inline-flex items-center gap-1.5">
          <DatabaseZap className="h-3 w-3 text-brass" />
          {symbols.length} simbolos / 5m
        </span>
      }
      className="min-h-[360px]"
    >
      {symbols.length === 0 ? (
        <EmptyState
          title="Universo pendiente"
          description="Esperando universo BingX y snapshots del scanner."
        />
      ) : (
        <div
          className={cn(
            "grid gap-2",
            drawerOpen
              ? "grid-cols-1"
              : "grid-cols-1 md:grid-cols-2 2xl:grid-cols-3",
          )}
        >
          {symbols.map((sym) => {
            const snap = snapBySymbol.get(sym) ?? emptySnap(sym);
            return (
              <BingxAssetCard
                key={sym}
                snapshot={snap}
                instrument={instrumentBySymbol.get(sym)}
                selected={selectedSymbol === sym}
                compact={drawerOpen}
                onClick={() => onSelectSymbol(sym)}
              />
            );
          })}
        </div>
      )}
    </TerminalPanel>
  );
}

function emptySnap(symbol: string): BingXSnapshotSummary {
  return {
    symbol,
    bars: 0,
    latest_close: null,
    volume_z_score: null,
    last_volume: null,
    interval: "5m",
    closes_recent: [],
  };
}
