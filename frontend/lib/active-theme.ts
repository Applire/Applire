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

// frontend/lib/active-theme.ts
//
// Server-side counterpart to the client ThemeProvider. The root layout calls
// loadActiveSchemeVars() during SSR and inlines the result on <html>, so the
// very first paint already uses the active DB palette instead of the static
// globals.css default. Without this, globals.css renders first and the client
// ThemeProvider swaps in the DB scheme a few frames later — a visible flash
// (GitHub issue #30).

import type { DerivedScheme } from "@/lib/theme";

// Resolved per-call (not at module load) so tests and runtime env both apply.
// Server-side fetches run inside the frontend container, so they cannot use the
// browser-facing NEXT_PUBLIC_API_URL (empty / relative via nginx). BACKEND_URL
// points at the backend over the internal Docker network.
function serverApiBase(): string {
  return (
    process.env.BACKEND_URL ??
    (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "")
  );
}

/** Fetch the active color scheme's derived CSS variables for pre-paint
 *  injection. Returns null when no scheme is active (404) or the backend is
 *  unreachable — in which case the static globals.css palette is used. */
export async function loadActiveSchemeVars(): Promise<DerivedScheme | null> {
  try {
    const res = await fetch(`${serverApiBase()}/api/admin/color-schemes/active`, {
      // Always reflect the current active scheme; never serve a stale palette.
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = await res.json();
    return (data.derived as DerivedScheme) ?? null;
  } catch {
    return null;
  }
}
