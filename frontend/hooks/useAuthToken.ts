import { useState } from "react";

export function useAuthToken() {
  const [token, setToken] = useState<string | null>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("auth_token");
    }
    return null;
  });

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
