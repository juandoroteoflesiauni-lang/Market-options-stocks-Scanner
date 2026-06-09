"use client";

import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { SignalItem } from "./SignalItem";
import { useSignalStream } from "@/hooks/useSignalStream";
import { useFunnelStore } from "@/store/funnelStore";

export function SignalFeed() {
  // Activate WebSocket connection
  useSignalStream();

  const signals = useFunnelStore((s) => s.signals);
  const isConnected = useFunnelStore((s) => s.isConnected);

  return (
    <Card>
      <CardHeader
        title="Execution Signals"
        subtitle="Phase D — Real-time signal stream"
        action={
          <div className="flex items-center gap-2">
            <div
              className={[
                "h-2 w-2 rounded-full transition-colors duration-300",
                isConnected
                  ? "bg-signal-buy shadow-[0_0_8px_rgba(0,212,170,0.6)] animate-pulse"
                  : "bg-signal-sell",
              ].join(" ")}
              title={isConnected ? "WebSocket connected" : "Disconnected"}
            />
            <Badge variant={isConnected ? "buy" : "sell"}>
              {isConnected ? "LIVE" : "OFFLINE"}
            </Badge>
          </div>
        }
      />

      {signals.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <div className="h-12 w-12 rounded-full bg-bg-elevated flex items-center justify-center mb-3">
            <svg
              className="h-6 w-6 text-text-muted"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z"
              />
            </svg>
          </div>
          <p className="text-sm text-text-secondary">No signals yet</p>
          <p className="text-xs text-text-muted mt-1">
            {isConnected
              ? "Waiting for Phase D to emit execution signals..."
              : "Connect to backend to receive live signals"}
          </p>
        </div>
      ) : (
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {signals.map((signal) => (
            <SignalItem key={signal.id} signal={signal} />
          ))}
        </div>
      )}
    </Card>
  );
}
