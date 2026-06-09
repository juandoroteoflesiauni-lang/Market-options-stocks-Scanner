import { FunnelOverview } from "@/components/dashboard/FunnelOverview";
import { SignalFeed } from "@/components/signals/SignalFeed";
import { SystemStatusBar } from "@/components/dashboard/SystemStatusBar";

export default function HomePage() {
  return (
    <div className="flex flex-col min-h-[calc(100vh-3.5rem)]">
      {/* Main content */}
      <div className="flex-1 px-6 py-6 space-y-6">
        {/* Funnel overview — 4 phase cards */}
        <FunnelOverview />

        {/* Execution signals feed */}
        <SignalFeed />
      </div>

      {/* System status bar — fixed bottom */}
      <SystemStatusBar />
    </div>
  );
}
