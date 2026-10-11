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

//
// US357 (ADR-023 amended 2026-10-11): with a session cookie on the incoming
// request, the person's own scheme is painted — `GET /api/color-schemes/effective`
// with that cookie forwarded. Without one, or on any failure, the instance
// default from the public `GET /api/admin/color-schemes/active` (the login page).
// The public endpoint never sees the cookie (SF-THEME.4).

import { cookies } from "next/headers";
import type { DerivedScheme } from "@/lib/theme";

// Mirrors backend `applire.auth.sessions.SESSION_COOKIE`.
export const SESSION_COOKIE = "applire_session";

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

async function fetchDerived(
  path: string,
  headers?: Record<string, string>,
): Promise<DerivedScheme | null> {
  try {
    const res = await fetch(`${serverApiBase()}${path}`, {
      // Always reflect the current scheme; never serve a stale palette — and
      // `no-store` keeps the layout dynamic, so no per-person HTML is cached.
      cache: "no-store",
      headers,
    });
    if (!res.ok) return null;
    const data = await res.json();
    return (data.derived as DerivedScheme) ?? null;
  } catch {
    return null;
  }
}

/** The session cookie of the request being rendered, or null (no request
 *  scope — tests, build — counts as signed out). */
async function sessionCookie(): Promise<string | null> {
  try {
    return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
  } catch {
    return null;
  }
}

/** Fetch the derived CSS variables to inline on <html> before first paint: the
 *  signed-in person's effective scheme, else the instance default. Returns null
 *  when neither resolves — the static globals.css palette is then used. */
export async function loadActiveSchemeVars(): Promise<DerivedScheme | null> {
  const session = await sessionCookie();
  if (session) {
    const own = await fetchDerived("/api/color-schemes/effective", {
      Cookie: `${SESSION_COOKIE}=${session}`,
    });
    if (own) return own;
  }
  return fetchDerived("/api/admin/color-schemes/active");
}
