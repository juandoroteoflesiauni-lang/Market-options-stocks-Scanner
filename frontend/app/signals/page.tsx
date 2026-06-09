import { SignalFeed } from "@/components/signals/SignalFeed";

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Signals — Deep Funnel Station",
  description: "Phase D execution signals — real-time trading signal stream",
};

export default function SignalsPage() {
  return (
    <div className="px-6 py-6">
      <SignalFeed />
    </div>
  );
}
