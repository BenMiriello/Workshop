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
import { pauseForRetry } from "../lib/retryPause";
import { probeSession } from "../lib/sessionProbe";
import { setCurrentUser } from "../lib/storage";

type Status = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: Status;
  user: AuthUser | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  logout: (everywhere?: boolean) => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      // The splash holds until the server ANSWERS who is signed in. A restart
      // or a dropped connection is asked again rather than read as signed out.
      const me = await probeSession(auth.fetchMe, pauseForRetry, () => alive);
      if (me === undefined || !alive) return;
      setUser(me);
      setStatus(me ? "authed" : "anon");
      // Namespaces every browser storage key to this user, and migrates any
      // legacy un-namespaced key into that namespace the first time a real
      // id resolves. See lib/storage.ts's header for why this call, here, is
      // what closes the pre-auth read case.
      setCurrentUser(me?.id ?? null);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await auth.login(email, password);
    setUser(u);
    setStatus("authed");
    setCurrentUser(u.id);
  }, []);

  const register = useCallback(
    async (email: string, password: string, name?: string) => {
      const u = await auth.register(email, password, name);
      setUser(u);
      setStatus("authed");
      setCurrentUser(u.id);
    },
    [],
  );

  const logout = useCallback(async (everywhere = false) => {
    await auth.logout(everywhere);
    setUser(null);
    setStatus("anon");
    setCurrentUser(null);
  }, []);

  const changePassword = useCallback(async (current: string, next: string) => {
    await auth.changePassword(current, next);
  }, []);

  const value = useMemo(
    () => ({ status, user, login, register, logout, changePassword }),
    [status, user, login, register, logout, changePassword],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
