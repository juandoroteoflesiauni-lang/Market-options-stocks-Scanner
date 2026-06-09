"use client";

import { useState, useEffect } from "react";

import { apiFetch, ApiError } from "@/lib/api";
import { useFunnelStore } from "@/store/funnelStore";

import type { CandidateSnapshot } from "@/store/types";

interface UseCandidatesReturn {
  candidates: CandidateSnapshot[];
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useCandidates(): UseCandidatesReturn {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fetchKey, setFetchKey] = useState(0);
  const candidates = useFunnelStore((s) => s.candidates);
  const setCandidates = useFunnelStore((s) => s.setCandidates);

  useEffect(() => {
    let isMounted = true;

    const fetchCandidates = async () => {
      setIsLoading(true);
      try {
        const data = await apiFetch<CandidateSnapshot[]>(
          "/api/scanner/candidates",
        );
        if (isMounted) {
          setCandidates(data);
          setError(null);
        }
      } catch (err) {
        if (isMounted) {
          const message =
            err instanceof ApiError
              ? `API ${err.status}: ${err.message}`
              : "Failed to load candidates";
          setError(message);
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    fetchCandidates();

    return () => {
      isMounted = false;
    };
  }, [setCandidates, fetchKey]);

  const refetch = () => setFetchKey((k) => k + 1);

  return { candidates, isLoading, error, refetch };
}
