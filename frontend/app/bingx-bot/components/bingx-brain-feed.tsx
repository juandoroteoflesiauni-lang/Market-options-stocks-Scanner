"use client";

import { Activity } from "lucide-react";

import { EmptyState, TerminalPanel } from "@/components/ui/terminal";
import type { BingXDecisionSummary } from "@/lib/bingx-bot-types";

import { BingxBrainEventCard } from "./bingx-brain-event-card";

interface BingxBrainFeedProps {
  decisions: BingXDecisionSummary[];
}

export function BingxBrainFeed({ decisions }: BingxBrainFeedProps) {
  return (
    <TerminalPanel
      title="Decision Tape"
      eyebrow="Cerebro bot"
      source={
        <span className="inline-flex items-center gap-1.5 text-warn">
          <Activity className="h-3 w-3" />
          feed live
        </span>
      }
      className="min-h-[360px] xl:max-w-[360px]"
    >
      <div className="flex max-h-[560px] flex-col gap-2 overflow-y-auto pr-1">
        {decisions.length === 0 ? (
          <EmptyState
            title="Scan pendiente"
            description="Esperando la primera decision del motor BingX."
          />
        ) : (
          [...decisions]
            .reverse()
            .map((decision, index) => (
              <BingxBrainEventCard
                key={`${decision.symbol}-${decision.timestamp}-${index}`}
                decision={decision}
              />
            ))
        )}
      </div>
    </TerminalPanel>
  );
}
