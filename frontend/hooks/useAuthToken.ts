/**
 * useAuthToken — Authentication hook for the QuantumAnalyzer web shell.
 *
 * Uses the backend cookie-based auth system (HMAC-signed qa_session cookie).
 * The browser sends the cookie automatically with credentials: "include".
 *
 * @module hooks/useAuthToken
 */

"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { fetchJson } from "@/lib/api-client";
import { AuthError } from "@/lib/api-client";

export interface OperatorProfile {
  username: string;
  display_name: string;
  role: string;
  email: string | null;
  expires_at: number;
}

interface UseAuthTokenReturn {
  user: OperatorProfile | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

export function useAuthToken(): UseAuthTokenReturn {
  const [user, setUser] = useState<OperatorProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const checkAuth = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await fetchJson<{ user: OperatorProfile }>(
        "/api/v1/auth/me",
        { timeoutMs: 10000, quiet: true },
      );
      setUser(data.user);
    } catch (err) {
      if (err instanceof AuthError) {
        setUser(null);
      } else {
        setError("Failed to check authentication status");
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const initialCheckDone = useRef(false);
  useEffect(() => {
    if (!initialCheckDone.current) {
      initialCheckDone.current = true;
      queueMicrotask(() => {
        checkAuth();
      });
    }
  }, [checkAuth]);

  const login = useCallback(async (username: string, password: string) => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await fetchJson<{ user: OperatorProfile }>(
        "/api/v1/auth/login",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
          timeoutMs: 15000,
        },
      );
      setUser(data.user);
    } catch (err) {
      if (err instanceof AuthError) {
        setError("Invalid credentials");
      } else {
        setError(err instanceof Error ? err.message : "Login failed");
      }
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetchJson<{ ok: boolean }>("/api/v1/auth/logout", {
        method: "POST",
        timeoutMs: 10000,
      });
    } catch {
      // Logout is best-effort — clear local state regardless
    } finally {
      setUser(null);
    }
  }, []);

  return {
    user,
    isAuthenticated: user !== null,
    isLoading,
    error,
    login,
    logout,
    clearError,
  };
}
