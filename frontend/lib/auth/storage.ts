// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Strawberry default D-9 — browser-local state is keyed by the user id, so two
 * people sharing one browser never see each other's gap dismissals or fine-tune
 * scope. Non-React callers read the id the `CurrentUserProvider` publishes here.
 * On sign-out every `applire.` key of localStorage and sessionStorage is cleared.
 */

let currentUserId: string | null = null;

export function setStorageUserId(id: string | null): void {
  currentUserId = id;
}

export function getStorageUserId(): string | null {
  return currentUserId;
}

/** `<base>.u.<user id>`; `<base>.u.anonymous` while no user is known. */
export function userScopedKey(base: string): string {
  return `${base}.u.${currentUserId ?? "anonymous"}`;
}

const PREFIXES = ["applire."];

/** Remove the per-user browser state (sign-out). Best-effort. */
export function clearUserBrowserState(): void {
  for (const store of [safeStore("localStorage"), safeStore("sessionStorage")]) {
    if (!store) continue;
    try {
      const doomed: string[] = [];
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        if (k && PREFIXES.some((p) => k.startsWith(p))) doomed.push(k);
      }
      doomed.forEach((k) => store.removeItem(k));
    } catch {
      /* storage unavailable */
    }
  }
}

function safeStore(name: "localStorage" | "sessionStorage"): Storage | null {
  try {
    return typeof window !== "undefined" ? window[name] : null;
  } catch {
    return null;
  }
}
