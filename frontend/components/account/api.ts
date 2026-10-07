// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The signed-in person's own credential routes (contract §3.2, §3.4, §3.5 —
 * `docs/dev/api-contract-strawberry.md`). Every call is relative and
 * `credentials: "same-origin"`: the session cookie is the credential, and an api
 * bearer is refused on these routes by design (`require_session_user`).
 *
 * Functions never throw on an HTTP status — they return the `Response` (or a
 * parsed body on success) so the card can map `error_code` to copy. A 401
 * `unauthenticated` is handled by the shell's global fetch patch (2a).
 */

import { API_BASE } from "@/lib/auth";

export type PersonalTokenScope = "agent" | "api";

export interface TokenItem {
  id: string;
  name: string;
  scope: PersonalTokenScope | "probe";
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface TokenCreated extends TokenItem {
  token: string;
}

export type ReauthAction = "account.delete" | "oidc.unlink";

function send(path: string, method: string, body?: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function listMyTokens(): Promise<TokenItem[] | null> {
  try {
    const res = await send("/api/me/tokens", "GET");
    if (!res.ok) return null;
    return ((await res.json()) as { tokens: TokenItem[] }).tokens;
  } catch {
    return null;
  }
}

export function createMyToken(name: string, scope: PersonalTokenScope): Promise<Response> {
  return send("/api/me/tokens", "POST", { name, scope });
}

export function revokeMyToken(id: string): Promise<Response> {
  return send(`/api/me/tokens/${encodeURIComponent(id)}`, "DELETE");
}

export function changePassword(current: string, next: string): Promise<Response> {
  return send("/api/auth/password", "POST", { current, new: next });
}

/** `DELETE /api/me/account` — with the password, or without after an SSO re-auth. */
export function deleteMyAccount(password?: string): Promise<Response> {
  return send("/api/me/account", "DELETE", password === undefined ? {} : { password });
}

export function startOidcLink(): Promise<Response> {
  return send("/api/me/oidc/link", "POST");
}

export function unlinkOidc(): Promise<Response> {
  return send("/api/me/oidc", "DELETE");
}

export function startReauth(action: ReauthAction, targetId: string): Promise<Response> {
  return send("/api/me/reauth/start", "POST", { action, target_id: targetId });
}

/** Instance probe (monitoring) tokens — admin only (contract §3.6). */
export async function listProbeTokens(): Promise<TokenItem[] | null> {
  try {
    const res = await send("/api/admin/probe-tokens", "GET");
    if (!res.ok) return null;
    return ((await res.json()) as { tokens: TokenItem[] }).tokens;
  } catch {
    return null;
  }
}

export function createProbeToken(name: string): Promise<Response> {
  return send("/api/admin/probe-tokens", "POST", { name });
}

export function revokeProbeToken(id: string): Promise<Response> {
  return send(`/api/admin/probe-tokens/${encodeURIComponent(id)}`, "DELETE");
}

/**
 * Where the IdP round-trip was started from — `/settings?oidc=failed` carries no
 * intent, so the page remembers it to word the failure (link vs. confirmation).
 * sessionStorage: this tab only, gone with it.
 */
const OIDC_INTENT_KEY = "applire.settings.oidcIntent";

export function rememberOidcIntent(intent: "link" | ReauthAction): void {
  try {
    sessionStorage.setItem(OIDC_INTENT_KEY, intent);
  } catch {
    /* storage blocked — the generic failure copy applies */
  }
}

export function takeOidcIntent(): string | null {
  try {
    const v = sessionStorage.getItem(OIDC_INTENT_KEY);
    sessionStorage.removeItem(OIDC_INTENT_KEY);
    return v;
  } catch {
    return null;
  }
}

/** Follow an `authorize_url` from `/api/me/oidc/link` or `/api/me/reauth/start`. */
export async function followAuthorizeUrl(res: Response): Promise<boolean> {
  try {
    const body = (await res.json()) as { authorize_url?: string };
    if (!body.authorize_url) return false;
    window.location.assign(body.authorize_url);
    return true;
  } catch {
    return false;
  }
}
