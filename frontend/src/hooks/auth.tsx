import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api, ApiError } from "@/services/api";
import type { User } from "@/types";

type AuthState = {
  user: User | null | undefined; // undefined = loading
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<User>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  useEffect(() => {
    const onUnauthorized = () => qc.setQueryData(["me"], null);
    window.addEventListener("testlab:unauthorized", onUnauthorized);
    return () => window.removeEventListener("testlab:unauthorized", onUnauthorized);
  }, [qc]);

  const value: AuthState = {
    user: me.isLoading ? undefined : (me.data ?? null),
    // Drop every cached query except `me` (clearing `me` would detach its live observer and the
    // new user would never reach React), then set `me` so routing sees the change immediately.
    login: async (email, password) => {
      const out = await api<{ user: User }>("/auth/login", { method: "POST", json: { email, password } });
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      qc.setQueryData(["me"], out.user);
    },
    logout: async () => {
      await api("/auth/logout", { method: "POST" });
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      qc.setQueryData(["me"], null);
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
