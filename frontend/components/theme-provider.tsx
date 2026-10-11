"use client";

// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.


import { createContext, useCallback, useContext, useEffect } from "react";
import { DERIVED_SCHEME_KEYS } from "@/lib/theme";
import { useCurrentUser } from "@/lib/auth/current-user";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/** Remove every editor-applied inline override so the static globals.css palette
 *  takes over. Without this, the admin editor's neutral placeholder palette would
 *  linger on the document after leaving the editor when no scheme can be applied. */
function clearInlineScheme(): void {
  for (const key of DERIVED_SCHEME_KEYS) {
    document.documentElement.style.removeProperty(key);
  }
}

interface ThemeContextValue {
  /** Call after activating or choosing a scheme to propagate it immediately
   *  without a page reload. */
  refreshTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({ refreshTheme: () => {} });

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

async function fetchDerived(path: string): Promise<Record<string, string> | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) return null;
    const data = await res.json();
    return (data.derived as Record<string, string>) ?? null;
  } catch {
    return null;
  }
}

/** US357 (ADR-023 amended 2026-10-11): a signed-in person gets their effective
 *  scheme (`/api/color-schemes/effective`, require_user); everyone else the
 *  instance default from the public `/api/admin/color-schemes/active`. The
 *  person-scoped call is made ONLY when signed in — a 401 from it would trip the
 *  global sign-in redirect (lib/auth/fetch-patch) on a signed-out page. */
async function applyScheme(signedIn: boolean): Promise<void> {
  const derived =
    (signedIn ? await fetchDerived("/api/color-schemes/effective") : null) ??
    (await fetchDerived("/api/admin/color-schemes/active"));
  if (!derived) {
    // No scheme to apply (e.g. an empty / freshly-reset DB, or the backend is
    // not ready). Strip any editor-applied overrides so we revert to the
    // globals.css palette instead of stranding the editor's neutral
    // placeholder app-wide.
    clearInlineScheme();
    return;
  }
  for (const [key, value] of Object.entries(derived)) {
    document.documentElement.style.setProperty(key, value);
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const { status } = useCurrentUser();
  const signedIn = status === "authenticated";

  const refreshTheme = useCallback(() => {
    void applyScheme(signedIn);
  }, [signedIn]);

  useEffect(() => {
    // While the session is still being read, the server-rendered palette on
    // <html> is already the right one (lib/active-theme.ts) — don't guess.
    if (status === "loading") return;
    void applyScheme(signedIn);
  }, [status, signedIn]);

  return (
    <ThemeContext.Provider value={{ refreshTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
