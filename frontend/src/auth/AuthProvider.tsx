// Auth state for the SPA. Hydrates any stored session at boot, exposes the
// current user, and provides login / register / logout. The guard in App reads
// `status` to decide between the loading spinner, the login screen, and the app.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as auth from "../lib/auth";
import type { AuthUser } from "../lib/auth";

type Status = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: Status;
  user: AuthUser | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      await auth.hydrate();
      const me = await auth.fetchMe();
      if (!alive) return;
      setUser(me);
      setStatus(me ? "authed" : "anon");
    })();
    return () => {
      alive = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await auth.login(email, password);
    setUser(u);
    setStatus("authed");
  }, []);

  const register = useCallback(
    async (email: string, password: string, name?: string) => {
      const u = await auth.register(email, password, name);
      setUser(u);
      setStatus("authed");
    },
    [],
  );

  const logout = useCallback(async () => {
    await auth.logout();
    setUser(null);
    setStatus("anon");
  }, []);

  const value = useMemo(
    () => ({ status, user, login, register, logout }),
    [status, user, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
