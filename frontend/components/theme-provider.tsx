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
  /** Call after activating a scheme to propagate it immediately without a page reload. */
  refreshTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({ refreshTheme: () => {} });

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

async function applyActiveScheme(): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/admin/color-schemes/active`);
    if (!res.ok) {
      // No scheme to apply (e.g. an empty / freshly-reset DB). Strip any
      // editor-applied overrides so we revert to the globals.css palette
      // instead of stranding the editor's neutral placeholder app-wide.
      clearInlineScheme();
      return;
    }
    const data = await res.json();
    const derived: Record<string, string> = data.derived;
    for (const [key, value] of Object.entries(derived)) {
      document.documentElement.style.setProperty(key, value);
    }
  } catch {
    // Network error or server not ready — revert to the globals.css palette.
    clearInlineScheme();
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const refreshTheme = useCallback(() => {
    applyActiveScheme();
  }, []);

  useEffect(() => {
    applyActiveScheme();
  }, []);

  return (
    <ThemeContext.Provider value={{ refreshTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
