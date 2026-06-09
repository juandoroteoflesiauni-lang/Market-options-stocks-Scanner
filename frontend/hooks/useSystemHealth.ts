"use client";

import { useState, useEffect, useRef } from "react";

import { apiFetch, ApiError } from "@/lib/api";
import { useFunnelStore } from "@/store/funnelStore";

import type { SystemHealth } from "@/store/types";

const POLL_INTERVAL_MS = 30_000;

interface UseSystemHealthReturn {
  health: SystemHealth | null;
  isLoading: boolean;
  error: string | null;
}

export function useSystemHealth(): UseSystemHealthReturn {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const health = useFunnelStore((s) => s.systemHealth);
  const setSystemHealth = useFunnelStore((s) => s.setSystemHealth);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(
    undefined,
  );

  useEffect(() => {
    let isMounted = true;

    const fetchHealth = async () => {
      try {
        const data = await apiFetch<SystemHealth>("/api/health");
        if (isMounted) {
          setSystemHealth(data);
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

    fetchHealth();
    intervalRef.current = setInterval(fetchHealth, POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      clearInterval(intervalRef.current);
    };
  }, [setSystemHealth]);

  return { health, isLoading, error };
}
