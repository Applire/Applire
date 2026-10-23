// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-081 cl. 9 amended 2026-10-22 (#786, ruling T-2 = A): the review steps of
 * the writing loop that did not run, as the status doors carry them
 * (`review_degradation` on `GET /api/cv/{id}/status` and
 * `GET /api/cover-letter/{id}/status`, and the MCP status tools).
 *
 * `null`/absent = not recorded (a document from before 0084, an agent-authored
 * render); `[]` = recorded, every review step answered. The surface renders a
 * note only for a non-empty list.
 */

export type DegradedStage = "review" | "correction";
export type DegradedReason = "timeout" | "truncated" | "malformed" | "shape" | "error";

export interface DegradedReviewStep {
  /** The writing loop's chain id (`cv_language`, `cover_letter`, …). */
  check: string;
  stage: DegradedStage;
  reason: DegradedReason;
  attempt?: number;
  count?: number;
}

/** The user word for a chain: what the step checks, never the chain id. */
export type DegradedCheckLabel = "language" | "draft" | "final" | "other";

const CHECK_LABEL: Record<string, DegradedCheckLabel> = {
  cv_language: "language",
  cv_language_recheck: "language",
  cv_tailoring: "draft",
  cover_letter: "draft",
  cv_terminal_review: "final",
  letter_terminal_review: "final",
};

export function degradedCheckLabel(check: string): DegradedCheckLabel {
  return CHECK_LABEL[check] ?? "other";
}

/**
 * One line per (label, stage): the language pass and its recheck are the same
 * check to the user, so two timeouts there read as one line, not two.
 */
export function degradationLines(
  steps: DegradedReviewStep[] | null | undefined,
): { label: DegradedCheckLabel; stage: DegradedStage; reason: DegradedReason }[] {
  const out: { label: DegradedCheckLabel; stage: DegradedStage; reason: DegradedReason }[] = [];
  for (const s of steps ?? []) {
    const label = degradedCheckLabel(s.check);
    if (out.some((l) => l.label === label && l.stage === s.stage)) continue;
    out.push({ label, stage: s.stage, reason: s.reason });
  }
  return out;
}
