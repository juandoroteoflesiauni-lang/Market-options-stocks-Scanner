import { CandidateTable } from "@/components/scanner/CandidateTable";

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Scanner — Deep Funnel Station",
  description: "Phase A scanner candidates — filtered market tickers",
};

export default function ScannerPage() {
  return (
    <div className="px-6 py-6">
      <CandidateTable />
    </div>
  );
}
