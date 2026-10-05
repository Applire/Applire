// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * US330 (Strawberry, ADR-091) — the auth REST shapes the frontend reads, and the
 * small helpers every auth page shares. Shapes mirror
 * `backend/applire/schemas/auth.py` (frozen interface F1,
 * `docs/dev/api-contract-strawberry.md` §3.2/§3.3).
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export type Role = "admin" | "user";

/** `GET /api/auth/me` — the signed-in person. */
export interface CurrentUser {
  id: string;
  email: string;
  role: Role;
  /** False for OIDC-only accounts (no password to change). */
  has_password: boolean;
  /** True when an OIDC (issuer, subject) binding exists. */
  oidc_linked: boolean;
  /** `user_settings.ui_language` when set, else null. */
  ui_language: string | null;
}

/** `GET /api/auth/state` — instance mode, readable before sign-in. */
export interface AuthState {
  setup_required: boolean;
  oidc_enabled: boolean;
  oidc_button_label: string;
  smtp_enabled: boolean;
  /** The test harness serves this instance — red banner on every page. */
  harness: boolean;
}

/** `POST /api/auth/links/inspect` */
export interface LinkInspect {
  purpose: "invite" | "reset";
  email: string;
  state: "valid" | "expired" | "used";
}

/** The `error_code` of a structured Strawberry error body, or null. */
export async function readErrorCode(res: Response): Promise<string | null> {
  try {
    const body = await res.clone().json();
    const code = body?.detail?.error_code;
    return typeof code === "string" ? code : null;
  } catch {
    return null;
  }
}

/** The English `message` of a structured error body (origin_mismatch shows it verbatim). */
export async function readErrorMessage(res: Response): Promise<string | null> {
  try {
    const body = await res.clone().json();
    const msg = body?.detail?.message;
    return typeof msg === "string" ? msg : null;
  } catch {
    return null;
  }
}

/** The pages a signed-out visitor may see — the 401 redirect never fires on them. */
export const AUTH_PAGES = ["/login", "/setup", "/invite", "/reset", "/forgot"] as const;

export function isAuthPage(pathname: string | null | undefined): boolean {
  const p = pathname ?? "";
  return AUTH_PAGES.some((page) => p === page || p.startsWith(page + "/"));
}

/**
 * A `?next=` value the login page may follow: a same-origin relative path
 * (`/…`, never `//…` or `/\…`), never an auth page (no loop). Anything else → `/`.
 */
export function safeNextPath(raw: string | null | undefined): string {
  const value = (raw ?? "").trim();
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (/[\u0000-\u001f]/.test(value)) return "/";
  const pathOnly = value.split(/[?#]/)[0];
  if (isAuthPage(pathOnly)) return "/";
  return value;
}

/** `/login?next=…` for the current location (query kept, fragment dropped). */
export function loginPathFor(
  current: { pathname: string; search: string },
  opts: { expired?: boolean } = {},
): string {
  const params = new URLSearchParams();
  const next = safeNextPath(current.pathname + (current.search ?? ""));
  if (next !== "/") params.set("next", next);
  if (opts.expired) params.set("expired", "1");
  const q = params.toString();
  return q ? `/login?${q}` : "/login";
}

/** A `next` that carries a share-target prefill (US229) — the link survives sign-in. */
export function isSharePrefillNext(next: string): boolean {
  try {
    const u = new URL(next, "http://x");
    return (
      u.pathname === "/share-target" ||
      (u.pathname === "/dashboard" && (u.searchParams.has("jd_url") || u.searchParams.has("jd_text")))
    );
  } catch {
    return false;
  }
}

export async function fetchAuthState(): Promise<AuthState | null> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/state`, { credentials: "same-origin" });
    return res.ok ? ((await res.json()) as AuthState) : null;
  } catch {
    return null;
  }
}

export type MeResult =
  | { kind: "user"; user: CurrentUser }
  | { kind: "signed-out" }
  | { kind: "error" };

export async function fetchMe(): Promise<MeResult> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`, { credentials: "same-origin" });
    if (res.ok) return { kind: "user", user: (await res.json()) as CurrentUser };
    if (res.status === 401) return { kind: "signed-out" };
    return { kind: "error" };
  } catch {
    return { kind: "error" };
  }
}

/** POST JSON to an auth endpoint; never throws on HTTP status (network errors do throw). */
export function postJson(path: string, body?: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
