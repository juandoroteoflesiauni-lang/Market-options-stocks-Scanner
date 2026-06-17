"use client";

import { useCallback, useEffect, useState } from "react";

import type { RoutePnLDashboardResponse } from "@/types/route-pnl";

export function useRoutePnL(limit = 200) {
  const [data, setData] = useState<RoutePnLDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/v1/route-pnl/summary?limit=${limit}`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const json = (await res.json()) as RoutePnLDashboardResponse;
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error cargando PnL");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
