// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 (ADR-091) — who is signed in, once per page load.
 *
 * `CurrentUserProvider` (mounted in `components/providers.tsx`) reads
 * `GET /api/auth/me` and `GET /api/auth/state` once; `useCurrentUser()` exposes
 * the person (role, email, has_password, oidc_linked, ui_language — F1), the
 * instance mode (OIDC label, SMTP, harness) and `refresh()` / `signOut()`.
 *
 * status:
 *   loading          — first answer not in yet
 *   authenticated    — `user` is set
 *   unauthenticated  — `/api/auth/me` answered 401
 *   error            — backend unreachable / 5xx: the shell renders its pages
 *                      anyway (an outage must not look like a sign-out)
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { API_BASE, fetchAuthState, fetchMe, type AuthState, type CurrentUser } from "./api";
import { markSessionKnown, suppressAuthRedirect } from "./fetch-patch";
import { clearUserBrowserState, setStorageUserId } from "./storage";

export type CurrentUserStatus = "loading" | "authenticated" | "unauthenticated" | "error";

export interface CurrentUserValue {
  status: CurrentUserStatus;
  user: CurrentUser | null;
  authState: AuthState | null;
  isAdmin: boolean;
  /** Re-read `/api/auth/me` (e.g. after a password change or OIDC link). */
  refresh: () => Promise<void>;
  /** POST /api/auth/logout, clear per-user browser state, go to `/login?signed_out=1`. */
  signOut: () => Promise<void>;
}

const noop = async () => {};

const DEFAULT_VALUE: CurrentUserValue = {
  status: "loading",
  user: null,
  authState: null,
  isAdmin: false,
  refresh: noop,
  signOut: noop,
};

const CurrentUserContext = createContext<CurrentUserValue>(DEFAULT_VALUE);

export function useCurrentUser(): CurrentUserValue {
  return useContext(CurrentUserContext);
}

export interface CurrentUserProviderProps {
  children: React.ReactNode;
  /**
   * Tests / stories: a fixed value — nothing is fetched. Partial values are
   * merged over a signed-in default (`isAdmin` derives from `user.role`).
   */
  value?: Partial<CurrentUserValue>;
}

export function CurrentUserProvider({ children, value }: CurrentUserProviderProps) {
  if (value) return <StaticCurrentUser value={value}>{children}</StaticCurrentUser>;
  return <LiveCurrentUser>{children}</LiveCurrentUser>;
}

function StaticCurrentUser({ value, children }: { value: Partial<CurrentUserValue>; children: React.ReactNode }) {
  const merged = useMemo<CurrentUserValue>(() => {
    const user = value.user ?? null;
    return {
      ...DEFAULT_VALUE,
      status: value.status ?? (user ? "authenticated" : "unauthenticated"),
      ...value,
      user,
      isAdmin: value.isAdmin ?? user?.role === "admin",
    };
  }, [value]);
  useEffect(() => {
    setStorageUserId(merged.user?.id ?? null);
  }, [merged.user]);
  return <CurrentUserContext.Provider value={merged}>{children}</CurrentUserContext.Provider>;
}

function LiveCurrentUser({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<CurrentUserStatus>("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [authState, setAuthState] = useState<AuthState | null>(null);

  const load = useCallback(async () => {
    const [me, state] = await Promise.all([fetchMe(), fetchAuthState()]);
    setAuthState(state);
    if (me.kind === "user") {
      setStorageUserId(me.user.id);
      markSessionKnown(true);
      setUser(me.user);
      setStatus("authenticated");
    } else {
      setStorageUserId(null);
      markSessionKnown(false);
      setUser(null);
      setStatus(me.kind === "signed-out" ? "unauthenticated" : "error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const signOut = useCallback(async () => {
    suppressAuthRedirect(true);
    try {
      await fetch(`${API_BASE}/api/auth/logout`, { method: "POST", credentials: "same-origin" });
    } catch {
      /* the cookie may already be gone — go to the sign-in page anyway */
    }
    clearUserBrowserState();
    setStorageUserId(null);
    markSessionKnown(false);
    window.location.assign("/login?signed_out=1");
  }, []);

  const value = useMemo<CurrentUserValue>(
    () => ({
      status,
      user,
      authState,
      isAdmin: user?.role === "admin",
      refresh: load,
      signOut,
    }),
    [status, user, authState, load, signOut],
  );

  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}
