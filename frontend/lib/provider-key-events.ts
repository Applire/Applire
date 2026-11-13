// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-096 — window events of the per-user AI provider key.
 *
 * `REQUIRED`: the global fetch patch saw a `409 provider_key_required |
 * provider_key_unreadable` on an `/api/` call (detail: `{ code }`).
 * `CHANGED`: the key card saved or removed a key — the shell banner re-reads.
 */

export const PROVIDER_KEY_REQUIRED_EVENT = "applire:provider-key-required";
export const PROVIDER_KEY_CHANGED_EVENT = "applire:provider-key-changed";

export const PROVIDER_KEY_ERROR_CODES = ["provider_key_required", "provider_key_unreadable"] as const;
export type ProviderKeyErrorCode = (typeof PROVIDER_KEY_ERROR_CODES)[number];

export function isProviderKeyErrorCode(code: string | null | undefined): code is ProviderKeyErrorCode {
  return code === "provider_key_required" || code === "provider_key_unreadable";
}
