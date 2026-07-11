// Copyright (C) 2024-2026 Tobias Rosenbaum
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
 * Utilities for the /match job ranking page.
 */

/** Score thresholds for colour-coding the combined-score bar. */
export const SCORE_GREEN_THRESHOLD = 0.7;
export const SCORE_AMBER_THRESHOLD = 0.4;

/** Return the Tailwind colour token for a combined score in [0, 1]. */
export function scoreColor(score: number): "success" | "warning" | "critical" {
  if (score >= SCORE_GREEN_THRESHOLD) return "success";
  if (score >= SCORE_AMBER_THRESHOLD) return "warning";
  return "critical";
}

/** Format a [0, 1] score as a percentage string e.g. "72%". */
export function formatScore(score: number): string {
  return `${Math.round(score * 100)}%`;
}

/** Return the hex / Tailwind class used for the progress bar fill. */
export function scoreBarClass(score: number): string {
  if (score >= SCORE_GREEN_THRESHOLD) return "bg-success";
  if (score >= SCORE_AMBER_THRESHOLD) return "bg-warning";
  return "bg-critical";
}

/** The three category lists a gap analysis exposes to the gaps screen. */
export interface GapCategories {
  category_a?: string[]; // direct matches
  category_b?: string[]; // likely matches (partials)
  category_c?: string[]; // gaps to address
}

/** The counts the gaps screen renders, derived once so no two surfaces disagree. */
export interface GapCounts {
  directMatches: number; // category_a
  likelyMatches: number; // active category_b (partials)
  gaps: number; // active category_c — the ONE canonical "gaps" number
  itemsToAddress: number; // partials + gaps — gates the section and the interview CTA
}

/** The slim ledger projection the gaps screen reads (ADR-048 fit-slice). */
export interface LedgerChipEntry {
  concept: string;
  fit_weight: number; // 1.0 required / 0.5 nice_to_have / 0.0 keyword-only
  sources?: string[];
}

/**
 * Canonical JD-echo chips (#111, blind PQ F6): when a Keyword Ledger exists,
 * the echo card lists and counts the SAME fit-weighted slice the badges and
 * the match score are computed from — the raw JD lists disagree with the
 * ledger math whenever concepts were merged or classification widened
 * ("17 requirements detected" vs "24 direct matches"). Pre-ledger analyses
 * fall back to the raw JD lists.
 */
export function canonicalRequirementChips(
  ledger: LedgerChipEntry[] | null | undefined,
  rawRequired: string[],
  rawNiceToHave: string[],
): { required: string[]; niceToHave: string[] } {
  const fitSlice = (ledger ?? []).filter((e) => (e.fit_weight ?? 0) > 0);
  if (fitSlice.length === 0) {
    return { required: rawRequired, niceToHave: rawNiceToHave };
  }
  return {
    required: fitSlice.filter((e) => e.fit_weight >= 1).map((e) => e.concept),
    niceToHave: fitSlice.filter((e) => e.fit_weight < 1).map((e) => e.concept),
  };
}

/**
 * Derive every gap count shown on the gaps screen from a single source.
 *
 * The `gaps` field is the one canonical "gaps" number — both the "gaps to
 * address" badge and the "gaps identified" heading read it, so they can never
 * disagree (F1). A likely match (partial) is NOT a gap; it only contributes to
 * `itemsToAddress`, which gates whether the interview/section is offered.
 * Tolerant of null / missing categories.
 */
export function gapCounts(
  gaps: GapCategories | null | undefined,
  resolved: Set<string>,
): GapCounts {
  const activeC = (gaps?.category_c ?? []).filter((g) => !resolved.has(g));
  const activeB = (gaps?.category_b ?? []).filter((g) => !resolved.has(g));
  return {
    directMatches: gaps?.category_a?.length ?? 0,
    likelyMatches: activeB.length,
    gaps: activeC.length,
    itemsToAddress: activeC.length + activeB.length,
  };
}
