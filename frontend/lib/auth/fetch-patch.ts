// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * US330 (ADR-091) — the global 401 handler.
 *
 * Patches `window.fetch` ONCE so the 58 calling files and `lib/api/*` need no
 * edit: a `401` from an Applire API URL whose body carries
 * `error_code: "unauthenticated"` (the ONLY code the shell acts on — contract §2)
 * sends the browser to `/login?next=<where you were>`. Every other status, every
 * other 401 code (`invalid_credentials` at login …) and every non-API URL passes
 * through untouched. The response is always returned to the caller unchanged.
 *
 * Never fires on the signed-out pages themselves (no redirect loop), never twice,
 * and not while a deliberate sign-out is navigating away.
 */

import { API_BASE, isAuthPage, loginPathFor, readErrorCode } from "./api";

type FetchFn = typeof fetch;

const PATCHED = Symbol.for("applire.authFetchPatched");

let redirecting = false;
let suppressed = false;
let sessionWasKnown = false;

/** Called once a signed-in user is known — a later 401 means the session ENDED. */
export function markSessionKnown(known: boolean): void {
  sessionWasKnown = known;
}

/** A deliberate sign-out navigates itself; the patch must not race it. */
export function suppressAuthRedirect(on: boolean): void {
  suppressed = on;
}

/** Test seam. */
export function resetAuthFetchStateForTests(): void {
  redirecting = false;
  suppressed = false;
  sessionWasKnown = false;
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return (input as Request).url;
}

/** Relative `/api/…`, `${API_BASE}/api/…`, or a same-origin absolute `/api/…`. */
export function isApiUrl(raw: string, origin: string = typeof window !== "undefined" ? window.location.origin : ""): boolean {
  if (raw.startsWith("/api/")) return true;
  if (API_BASE && raw.startsWith(`${API_BASE}/api/`)) return true;
  try {
    const u = new URL(raw);
    return u.origin === origin && u.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

export async function handleUnauthenticated(res: Response, url: string): Promise<boolean> {
  if (res.status !== 401 || redirecting || suppressed) return false;
  if (!isApiUrl(url)) return false;
  if (isAuthPage(window.location.pathname)) return false;
  const code = await readErrorCode(res);
  if (code !== "unauthenticated") return false;
  if (redirecting || suppressed) return false;
  redirecting = true;
  window.location.assign(
    loginPathFor(
      { pathname: window.location.pathname, search: window.location.search },
      { expired: sessionWasKnown },
    ),
  );
  return true;
}

/** Idempotent. Wraps whatever `window.fetch` is at call time. */
export function installAuthFetch(): void {
  if (typeof window === "undefined" || typeof window.fetch !== "function") return;
  const current = window.fetch as FetchFn & { [PATCHED]?: boolean };
  if (current[PATCHED]) return;
  const original: FetchFn = current.bind(window);
  const patched = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const res = await original(input, init);
    if (res.status === 401) {
      // Not awaited: the caller gets its response at once; the redirect follows.
      void handleUnauthenticated(res, requestUrl(input)).catch(() => {});
    }
    return res;
  }) as FetchFn & { [PATCHED]?: boolean };
  patched[PATCHED] = true;
  window.fetch = patched;
}
