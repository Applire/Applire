// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #686 — which decisions the candidate has waved away, per flow visit.
//
// Founder ruling V-1 (2026-09-09) made the surface a toast-style stack of one
// popup PER DECISION, so dismissal is per decision too: waving away the one you
// have already thought about must not hide the two you have not. The store is
// therefore a SET of issue ids per flow, not a single boolean.
//
// **Auto-hide is not dismissal.** The timer that hides the stack after
// `notice_auto_dismiss_seconds` writes nothing here: an untouched popup comes
// back on the next mount, because "I did not react in 30 seconds" is not an
// answer. Only the × records a decision to stop being shown one.
//
// Same shape as `lib/review-walked.ts`: a namespaced key, every access wrapped,
// and a benign default when storage is unavailable (private mode, storage
// disabled) — which for this surface is "still show it".

const KEY_PREFIX = "applire.gaps.decisionsDismissed.";

export function decisionsDismissedKey(flowId: string): string {
  return `${KEY_PREFIX}${flowId}`;
}

/** The issue ids dismissed for THIS flow in THIS browser. */
export function dismissedDecisions(flowId: string | null | undefined): Set<string> {
  if (!flowId) return new Set();
  try {
    const raw = window.localStorage.getItem(decisionsDismissedKey(flowId));
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    // Unavailable storage, or a value some earlier version wrote in another
    // shape. Both mean "nothing is known to be dismissed" — show them.
    return new Set();
  }
}

/** Record that this ONE decision was waved away for this flow. Resolves nothing. */
export function markDecisionDismissed(
  flowId: string | null | undefined,
  issueId: string,
): void {
  if (!flowId || !issueId) return;
  try {
    const next = dismissedDecisions(flowId);
    next.add(issueId);
    window.localStorage.setItem(
      decisionsDismissedKey(flowId),
      JSON.stringify([...next]),
    );
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
