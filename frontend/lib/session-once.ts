// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Hazelnut build 3 (WP-F2, #789) — "once per document set per browser session".
 *
 * The pre-download AI notice and the "Application submitted?" prompt are right
 * the first time and friction every time after (UX audit 2026-10-20: three
 * notices and three prompts for one application). Both now remember, for THIS
 * browser session and THIS signed-in user, that the set (one application's CV +
 * letter) has already shown them. `sessionStorage`, keyed per user (D-9) and
 * cleared on sign-out — a new session or another application asks again.
 *
 * Never a gate: storage that throws (private mode, blocked site data) reads as
 * "not seen", so the worst case is the old behaviour — the notice once more.
 */
import { userScopedKey } from "@/lib/auth/storage";

export type OncePerSetKind = "predownload-notice" | "mark-applied-prompt";

function key(kind: OncePerSetKind, setId: string): string {
  return userScopedKey(`applire.once.${kind}.${setId}`);
}

export function seenThisSession(kind: OncePerSetKind, setId: string | null | undefined): boolean {
  if (!setId) return false;
  try {
    return window.sessionStorage.getItem(key(kind, setId)) === "1";
  } catch {
    return false;
  }
}

export function markSeenThisSession(kind: OncePerSetKind, setId: string | null | undefined): void {
  if (!setId) return;
  try {
    window.sessionStorage.setItem(key(kind, setId), "1");
  } catch {
    /* storage unavailable — the notice simply shows again next time */
  }
}
