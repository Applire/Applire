// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Admin people routes (contract §3.6, `require_admin`). Metadata only — the list
 * never carries profile content (S-4); a name is vault content, so there is no
 * name column (W0B-1). Functions return the `Response` so the page can map the
 * `error_code` (`email_taken`, `last_admin`, …) to copy.
 */

import { API_BASE } from "@/lib/auth";

export type UserStatus = "pending" | "active" | "disabled";

export interface AdminUserMetadata {
  application_count: number;
  document_count: number;
  storage_bytes: number | null;
  ai_tokens_30d: number | null;
}

export interface AdminUser {
  id: string;
  email: string;
  role: "admin" | "user";
  status: UserStatus;
  created_at: string;
  last_login_at: string | null;
  last_active_at: string | null;
  invite_expires_at: string | null;
  metadata: AdminUserMetadata;
}

export interface IssuedLink {
  purpose: "invite" | "reset";
  url: string;
  expires_at: string;
  mailed: boolean;
  mail_failed: boolean;
}

function send(path: string, method: string, body?: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const id = (userId: string) => encodeURIComponent(userId);

export async function listUsers(): Promise<AdminUser[] | null> {
  try {
    const res = await send("/api/admin/users", "GET");
    if (!res.ok) return null;
    return ((await res.json()) as { users: AdminUser[] }).users;
  } catch {
    return null;
  }
}

/** MD-28: `send_mail` is sent explicitly both ways — it only decides about the mail. */
export function createUser(email: string, role: "admin" | "user", sendMail: boolean): Promise<Response> {
  return send("/api/admin/users", "POST", { email, role, send_mail: sendMail });
}

export function patchUser(userId: string, patch: { role?: "admin" | "user"; disabled?: boolean }): Promise<Response> {
  return send(`/api/admin/users/${id(userId)}`, "PATCH", patch);
}

export function deleteUser(userId: string): Promise<Response> {
  return send(`/api/admin/users/${id(userId)}`, "DELETE");
}

export function reinvite(userId: string): Promise<Response> {
  return send(`/api/admin/users/${id(userId)}/reinvite`, "POST");
}

export function resetLink(userId: string): Promise<Response> {
  return send(`/api/admin/users/${id(userId)}/reset-link`, "POST");
}

export function revokeTokens(userId: string): Promise<Response> {
  return send(`/api/admin/users/${id(userId)}/revoke-tokens`, "POST");
}
