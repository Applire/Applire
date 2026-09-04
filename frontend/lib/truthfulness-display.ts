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
 * The Truthfulness Oracle report's SHAPE and the ONE display rule that decides
 * which claims count as *flagged*.
 *
 * Extracted from `components/cv/TruthfulnessPanel.tsx` by E058/US300. ADR-081
 * clause 2's group 1 is the union of the Oracle's flagged claims and the ATS
 * report's `present_unsupported` terms, so the review surface has to ask the
 * same question the panel asks — and asking it with a second copy of the rule
 * is exactly the `SF-DOOR.7` class this epic exists to remove (a rule
 * duplicated in the UX layer and drifting). One implementation, here; the panel
 * and the surface both import it (ADR-066).
 *
 * Nothing about the Oracle's own verdict taxonomy changes. `related` is and
 * stays a FRONTEND-ONLY display state (#249 / ADR-052 §3): it reclassifies how
 * an `unbacked` skill claim RENDERS when the Keyword Ledger already vouches for
 * the same concept, never what the Oracle concluded.
 */

export type Verdict =
  | "grounded"
  | "inflated"
  | "misattributed"
  | "unbacked"
  | "unverifiable"
  // #237 round-3: statements ABOUT the target employer (sourced from the JD,
  // validated by the ADR-021 reviewer) — the vault can't ground them, so the
  // Oracle files them as not_applicable and excludes them from dominance.
  | "not_applicable";

export type TruthfulnessEvidence = {
  kind: "profile_path" | "enrichment_record";
  ref: string;
  excerpt?: string;
};

export type TruthfulnessClaimResult = {
  claim: { text: string; location: string; kind: string };
  verdict: {
    verdict: Verdict;
    checker: string;
    evidence: TruthfulnessEvidence[];
    detail?: string | null;
  };
};

export type TruthfulnessReport = {
  version: string;
  document_kind: string;
  claims: TruthfulnessClaimResult[];
  counts: Record<string, number>;
  stated_limit: string;
  // #249/US266: a report-level summary flag (>50% unverifiable). Older
  // persisted reports simply lack the field — absent === false, never a crash.
  unverifiable_dominated?: boolean;
  // ADR-068 (SF-ORACLE.3 report-side control): count of claims whose model
  // judgement could not run — provider failure/degradation. Optional; absent
  // on older reports (=> no notice, ever).
  judgement_unavailable?: number;
} | null;

export const FLAG_VERDICTS: Verdict[] = ["inflated", "misattributed", "unbacked"];

/**
 * Simple, deterministic client-side fold (#249: "keep it simple and
 * deterministic") — case and surrounding whitespace only, no fuzzy matching.
 *
 * Deliberately NOT `normQuote`: this fold decides whether the Keyword Ledger
 * vouches for a skill string, and widening it would silently change which
 * claims the Oracle's red headline counts. `normQuote` (ADR-070 cl. 1) is the
 * separate, narrower job of ADR-081 clause 2's group-1 carve-out.
 */
export function foldSkillText(s: string): string {
  return s.trim().toLowerCase();
}

/** The claimable Keyword Ledger concepts, folded, from the sibling ATS report. */
export function claimableConceptSet(concepts: string[] | undefined): Set<string> {
  return new Set((concepts ?? []).map(foldSkillText));
}

/**
 * An `unbacked` SKILL claim whose text matches a claimable Keyword Ledger
 * concept has RELATED vault evidence, just not a literal hit — a third state
 * that is neither a red flag nor a green pass (#249). Excluded from the flagged
 * set here, exactly as `TruthfulnessPanel` excludes it from its red headline.
 */
export function isRelatedClaim(
  c: TruthfulnessClaimResult,
  claimableSet: Set<string>,
): boolean {
  return (
    c.verdict.verdict === "unbacked" &&
    c.claim.kind === "skill" &&
    claimableSet.has(foldSkillText(c.claim.text))
  );
}

/** The claims the Oracle flags for review — the group-1 half of ADR-081 cl. 2. */
export function flaggedClaims(
  claims: TruthfulnessClaimResult[],
  claimableSet: Set<string>,
): TruthfulnessClaimResult[] {
  return claims.filter(
    (c) => FLAG_VERDICTS.includes(c.verdict.verdict) && !isRelatedClaim(c, claimableSet),
  );
}
