import { useState, useEffect } from "react";

export function useAuthToken() {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    // Phase 1 implementation: Just mock or retrieve from localStorage
    const storedToken = localStorage.getItem("auth_token");
    setToken(storedToken);
  }, []);

  const saveToken = (newToken: string) => {
    localStorage.setItem("auth_token", newToken);
    setToken(newToken);
  };

  const clearToken = () => {
    localStorage.removeItem("auth_token");
    setToken(null);
  };

  return { token, saveToken, clearToken };
}
