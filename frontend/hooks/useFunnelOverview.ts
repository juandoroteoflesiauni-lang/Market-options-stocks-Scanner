"use client";

import { useState, useEffect, useRef } from "react";

import { apiFetch, ApiError } from "@/lib/api";
import { useFunnelStore } from "@/store/funnelStore";

import type { FunnelOverview } from "@/store/types";

const POLL_INTERVAL_MS = 10_000;

interface UseFunnelOverviewReturn {
  overview: FunnelOverview | null;
  isLoading: boolean;
  error: string | null;
}

export function useFunnelOverview(): UseFunnelOverviewReturn {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const overview = useFunnelStore((s) => s.funnelOverview);
  const setFunnelOverview = useFunnelStore((s) => s.setFunnelOverview);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(
    undefined,
  );

  useEffect(() => {
    let isMounted = true;

    const fetchOverview = async () => {
      try {
        const data = await apiFetch<FunnelOverview>("/api/funnel/overview");
        if (isMounted) {
          setFunnelOverview(data);
          setError(null);
          setIsLoading(false);
        }
      } catch (err) {
        if (isMounted) {
          const message =
            err instanceof ApiError
              ? `API ${err.status}: ${err.message}`
              : "Connection failed";
          setError(message);
          setIsLoading(false);
        }
      }
    };

    fetchOverview();
    intervalRef.current = setInterval(fetchOverview, POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      clearInterval(intervalRef.current);
    };
  }, [setFunnelOverview]);

  return { overview, isLoading, error };
}
