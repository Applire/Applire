// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

/**
 * ADR-081 clause 5 (+ clause 5a, amendment 2026-09-04) — which review mode a
 * document opens in.
 *
 * `user_settings.review_mode` is a three-valued PERSON-level preference:
 * `overview` and `guided` fix the mode; `auto` — the default — makes the mode a
 * property of the DOCUMENT. Guided answers *"walk me through what is new"*,
 * which is true after a fresh generation and false on the fifth visit to the
 * same document, for a power user exactly as much as for a newcomer.
 *
 * **Clause 5a — the bit is keyed on the GENERATED-DOCUMENT id, never on the
 * application or the flow.** Clause 5 as accepted said "per document and per
 * browser" without saying what identifies the document, and `JF-F-K.3` recorded
 * that silence as a gap in the decision: keyed on the application, a
 * regeneration inherits `walked = true`, so a document whose findings are all
 * new resolves to overview and the guided walk it warranted never happens —
 * with nothing on screen saying the state was inherited. Keyed on the generated
 * document, a regeneration produces a new id, carries no bit, and returns to
 * guided.
 *
 * Storage stays browser-local by decision. The accepted cost is that a second
 * device (or cleared site data) costs ONE extra walk-through — the benign
 * direction, and the reason the field was not promoted to the server.
 */

export type ReviewModePreference = "auto" | "overview" | "guided";
export type ResolvedReviewMode = "overview" | "guided";
export type ReviewDocumentKind = "cv" | "cover-letter";

export const REVIEW_MODE_PREFERENCES: readonly ReviewModePreference[] = [
  "auto",
  "overview",
  "guided",
] as const;

export function isReviewModePreference(value: unknown): value is ReviewModePreference {
  return typeof value === "string" && (REVIEW_MODE_PREFERENCES as readonly string[]).includes(value);
}

const KEY_PREFIX = "applire.review.walked.";

/**
 * The storage key. Clause 5a: `<kind>.<generated-document id>`. The kind is in
 * the key only so a CV and a cover letter cannot collide if a backend ever
 * reuses an id space — it is NOT a second identity: two documents of the same
 * kind are distinguished by their ids alone.
 */
export function walkedStorageKey(kind: ReviewDocumentKind, documentId: string): string {
  return `${KEY_PREFIX}${kind}.${documentId}`;
}

/** Has THIS generated document's group-1 queue been walked in THIS browser? */
export function isDocumentWalked(kind: ReviewDocumentKind, documentId: string | null): boolean {
  if (!documentId) return false;
  try {
    return window.localStorage.getItem(walkedStorageKey(kind, documentId)) === "1";
  } catch {
    // Private mode / storage disabled — the benign direction is "not walked".
    return false;
  }
}

/** Record that this generated document's group-1 findings have been walked. */
export function markDocumentWalked(kind: ReviewDocumentKind, documentId: string | null): void {
  if (!documentId) return;
  try {
    window.localStorage.setItem(walkedStorageKey(kind, documentId), "1");
  } catch {
    // Best-effort by decision: a failure costs one extra walk-through.
  }
}

/** Test/erasure helper — never called from the UI. */
export function clearDocumentWalked(kind: ReviewDocumentKind, documentId: string | null): void {
  if (!documentId) return;
  try {
    window.localStorage.removeItem(walkedStorageKey(kind, documentId));
  } catch {
    /* ignore */
  }
}

export interface ResolveReviewModeInput {
  /** The stored `user_settings.review_mode`. */
  preference: ReviewModePreference;
  kind: ReviewDocumentKind;
  /** The GENERATED-DOCUMENT id (clause 5a). `null` while the document is not yet known. */
  documentId: string | null;
  /** Does this document have group-1 findings at all? */
  hasGroup1Findings: boolean;
}

/**
 * Resolve the mode the panel opens in. Pure — the only impure part is the
 * `localStorage` read, injected via `isDocumentWalked` so a test can drive it
 * without a browser.
 */
export function resolveReviewMode(
  input: ResolveReviewModeInput,
  walkedLookup: (kind: ReviewDocumentKind, documentId: string | null) => boolean = isDocumentWalked,
): ResolvedReviewMode {
  if (input.preference === "overview") return "overview";
  if (input.preference === "guided") return "guided";
  // `auto` follows the document.
  if (!input.documentId) return "overview";
  if (!input.hasGroup1Findings) return "overview";
  return walkedLookup(input.kind, input.documentId) ? "overview" : "guided";
}
