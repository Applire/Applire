// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #686 — "hides the card for this flow visit" (the issue's own wording).
//
// Same shape as `lib/review-walked.ts`: a namespaced localStorage key, every
// access wrapped, and a benign default when storage is unavailable. A bare
// `useState` (the `HealthPanel` nudge's shape) resets on every remount, which
// reads as the card ignoring the ×.
//
// Scoped per flow deliberately: dismissing the note while working one
// application must not hide a dispute raised in the next one.

const KEY_PREFIX = "applire.gaps.decisionsDismissed.";

export function decisionsDismissedKey(flowId: string): string {
  return `${KEY_PREFIX}${flowId}`;
}

/** Has the decisions card been dismissed for THIS flow in THIS browser? */
export function isDecisionsDismissed(flowId: string | null | undefined): boolean {
  if (!flowId) return false;
  try {
    return window.localStorage.getItem(decisionsDismissedKey(flowId)) === "1";
  } catch {
    // Private mode / storage disabled — the benign direction is "still show it".
    return false;
  }
}

/** Record the dismissal. Resolves nothing: the dispute stays parked. */
export function markDecisionsDismissed(flowId: string | null | undefined): void {
  if (!flowId) return;
  try {
    window.localStorage.setItem(decisionsDismissedKey(flowId), "1");
  } catch {
    // Best-effort by decision: a failure costs one extra dismissal.
  }
}

/** Test helper — never called from the UI. */
export function clearDecisionsDismissed(flowId: string | null | undefined): void {
  if (!flowId) return;
  try {
    window.localStorage.removeItem(decisionsDismissedKey(flowId));
  } catch {
    /* ignore */
  }
}
