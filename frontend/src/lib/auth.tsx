import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "@/lib/api";
import type { PublicUser, Role } from "@/lib/types";

/** Where a signed-in user lands, based on their role. */
export function homeFor(role: Role): string {
  return role === "admin" ? "/app" : "/order";
}

interface AuthContextValue {
  user: PublicUser | null;
  /** True until the initial session-restore (fetchMe) settles, so guards don't flash. */
  initializing: boolean;
  signup: (username: string, password: string, role: Role) => Promise<void>;
  signin: (username: string, password: string) => Promise<void>;
  signout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [user, setUser] = useState<PublicUser | null>(null);
  const [initializing, setInitializing] = useState(true);

  // On mount, restore the session from a stored token by asking the backend who we are.
  // A bad/expired token makes fetchMe 401, which api.asJson already clears for us.
  useEffect(() => {
    let cancelled = false;
    if (!api.getToken()) {
      setInitializing(false);
      return;
    }
    api
      .fetchMe()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        if (!cancelled) {
          api.clearToken();
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setInitializing(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signin = useCallback(
    async (username: string, password: string) => {
      const res = await api.login(username, password);
      api.setToken(res.token);
      setUser(res.user);
      navigate(homeFor(res.user.role), { replace: true });
    },
    [navigate],
  );

  const signup = useCallback(
    async (username: string, password: string, role: Role) => {
      const res = await api.signup(username, password, role);
      api.setToken(res.token);
      setUser(res.user);
      navigate(homeFor(res.user.role), { replace: true });
    },
    [navigate],
  );

  const signout = useCallback(() => {
    api.clearToken();
    setUser(null);
    navigate("/signin", { replace: true });
  }, [navigate]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, initializing, signup, signin, signout }),
    [user, initializing, signup, signin, signout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}
