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
 * ADR-081 clause 2 — findings grouped by the USER'S QUESTION, never by the
 * producing subsystem. A pure module: it takes the producers' persisted
 * payloads and returns four ordered groups. No fetching, no React, no state.
 *
 * | # | The user's question                                  | Composed from                                             |
 * |---|------------------------------------------------------|-----------------------------------------------------------|
 * | 1 | What is in the document my vault does not cover?      | `keywords.present_unsupported` + Oracle-flagged claims     |
 * | 2 | What is missing although my vault covers it?          | `keywords.missing_claimable` residue (+ claimable clusters)|
 * | 3 | What is missing, and my vault does not cover it?      | `keywords.missing_honest_gap` + gap-analysis clusters      |
 * | 4 | Is the craft sound?                                   | ATS structure checks + critic advisories                   |
 *
 * **This module reads; it does not compute.** Every population above is the
 * producer's own, unmodified. There is exactly ONE cross-producer suppression
 * anywhere on this surface, and it lives here: within group 1, an Oracle-flagged
 * claim and a `present_unsupported` term that fold to the same string under the
 * ADR-070 clause 1 quote fold (`normQuote`) become ONE row citing both
 * producers. Without it, clause 4's verdict double-counts — the two populations
 * are not disjoint, a skill with no vault evidence can legitimately appear in
 * both (`SF-REVIEW.3`). Nothing else is merged, deduplicated or reconciled.
 *
 * **Producer liveness (ADR-081 clause 9, `SF-REVIEW.4`).** Every group carries
 * the set of its producers that did not run. A group whose producers ALL failed
 * to run is `unknown`: the surface renders *unbekannt*, never `0`, keeps it out
 * of the passed-checks collapse, and keeps it out of the verdict sentence's
 * number. "0 claims need review" because the Oracle never ran must never look
 * like "0 claims need review" because it ran and found nothing.
 */

import type { GapHintItem } from "@/components/cv/ContentTab";
import type { CriticAdvisory, OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import type { ATSCheck, ATSReport } from "./ats-report";
import { normQuote } from "./norm-quote";
import {
  claimableConceptSet,
  flaggedClaims,
  type TruthfulnessReport,
} from "./truthfulness-display";

/** A subsystem that feeds this surface. Liveness is tracked per producer. */
export type ReviewProducer = "ats" | "oracle" | "critic" | "clusters";

/**
 * What KIND of thing a row is, as the user sees it. Group 3 renders two
 * granularities under one heading and must label each item with its origin
 * (`Cluster` / `Begriff`) rather than fusing them — fusing would be the UX
 * layer computing a backend rule, the `SF-DOOR.7` defect ADR-081 cl. 2 exists
 * to avoid repeating.
 */
export type ReviewItemKind = "term" | "cluster" | "claim" | "check" | "advisory";

export type ReviewSeverity = "critical" | "warning" | "info" | "neutral";

export interface ReviewItem {
  /** Stable React key AND the row's testid suffix. */
  key: string;
  label: string;
  kind: ReviewItemKind;
  /** Which producers assert this row. Two entries === the clause-2 carve-out fired. */
  producers: ReviewProducer[];
  severity: ReviewSeverity;
  /** Producer-supplied detail, rendered verbatim. Never composed here. */
  detail?: string | null;
  /** For a cluster row: the gap id, so the surface can route to its existing handler. */
  clusterId?: string;
  /** For a check row: the check id, so the surface can label it via `ats.checks.*`. */
  checkId?: string;
  /**
   * For a check row: the producer's own check object, carried through verbatim
   * so the surface renders the ATS auditor's `details` / `details_key` rather
   * than a second rendering of the same fact.
   */
  check?: ATSCheck;
  /** For a critic row: the advisory, carried through verbatim (ADR-060). */
  advisory?: CriticAdvisory;
  /** For a claim row: where in the document the Oracle found it. */
  location?: string | null;
}

export interface ReviewGroup {
  id: 1 | 2 | 3 | 4;
  items: ReviewItem[];
  /** Producers this group draws on. */
  producers: ReviewProducer[];
  /** ADR-081 cl. 9 — those of `producers` that did not run. */
  unknownProducers: ReviewProducer[];
  /**
   * True when EVERY producer of this group failed to run. Such a group renders
   * *unknown* instead of a count, never enters the passed-checks collapse, and
   * never contributes a number to the verdict sentence.
   */
  unknown: boolean;
  /**
   * Group 4 only — plain passing checks. ADR-081 cl. 6 permits the all-clear
   * collapse for PASSING CHECKS ONLY, so these are deliberately kept out of
   * `items` and out of the group's count: a count that is dominated by passes
   * is not a detection signal (`JF-M-6.4`).
   */
  passedChecks: number;
}

export interface ReviewInputs {
  atsReport: ATSReport;
  truthReport: TruthfulnessReport;
  criticReport: OutcomeCriticReport;
  /**
   * The gap-analysis clusters (§5.3.26). An empty array means "loaded, none
   * found"; `null` means "the producer exists for this document and did NOT
   * run", which renders as *unknown* rather than as zero (ADR-081 cl. 9).
   */
  gapClusters: GapHintItem[] | null;
  /**
   * Does this document HAVE a gap-cluster producer at all? The cover letter
   * does not — §5.3.26's clusters are computed against the CV. Passing `false`
   * removes `clusters` from groups 2 and 3's producer list entirely, so the
   * surface neither claims the clusters are empty nor claims they are unknown.
   * "This producer does not apply here" is a third state, and conflating it
   * with either of the other two would be a lie in one direction or the other.
   * Defaults to `true`.
   */
  hasClusterProducer?: boolean;
}

/** ADR-060: the outcome critic is an EXCEPTION surface — absent or `ran: false` both mean it did not run. */
function criticRan(report: OutcomeCriticReport): boolean {
  return Boolean(report && report.ran);
}

function dedupeClusters(items: GapHintItem[]): GapHintItem[] {
  const seen = new Set<string>();
  return items.filter((g) => {
    if (seen.has(g.id)) return false;
    seen.add(g.id);
    return true;
  });
}

/**
 * Group 1 — the send-blocking class, and the only place a cross-producer
 * suppression is permitted (ADR-081 cl. 2, `SF-REVIEW.3`).
 *
 * The ATS term is listed first and the Oracle claim folded into it when the two
 * normalise equal, so the row order is the ATS report's order and is stable
 * across renders. A claim that folds to no term keeps its own row.
 */
function buildGroup1(inputs: ReviewInputs): ReviewItem[] {
  const { atsReport, truthReport } = inputs;
  const terms = atsReport?.keywords.present_unsupported ?? [];
  const claimable = claimableConceptSet(atsReport?.keywords.claimable_concepts);
  const claims = truthReport ? flaggedClaims(truthReport.claims ?? [], claimable) : [];

  const claimsByNorm = new Map<string, number>();
  claims.forEach((c, i) => {
    const n = normQuote(c.claim.text);
    if (!claimsByNorm.has(n)) claimsByNorm.set(n, i);
  });

  const consumedClaims = new Set<number>();
  const items: ReviewItem[] = [];

  terms.forEach((term, i) => {
    const n = normQuote(term);
    const claimIndex = claimsByNorm.get(n);
    const overlap = claimIndex !== undefined && !consumedClaims.has(claimIndex);
    if (overlap) consumedClaims.add(claimIndex);
    const claim = overlap ? claims[claimIndex] : null;
    items.push({
      key: `term-${i}-${n || term}`,
      label: term,
      kind: "term",
      producers: overlap ? ["ats", "oracle"] : ["ats"],
      severity: "critical",
      detail: claim?.verdict.detail ?? null,
      location: claim?.claim.location ?? null,
    });
  });

  claims.forEach((c, i) => {
    if (consumedClaims.has(i)) return;
    items.push({
      key: `claim-${i}-${normQuote(c.claim.text) || c.claim.text}`,
      label: c.claim.text,
      kind: "claim",
      producers: ["oracle"],
      severity: "critical",
      detail: c.verdict.detail ?? null,
      location: c.claim.location,
    });
  });

  return items;
}

/**
 * Group 2 — the `missing_claimable` residue AFTER the coverage loop tried and
 * rank or budget won (ADR-076). No action is offered; the copy names the trade.
 *
 * Gap clusters the producer itself calls `claimable` belong here and not in
 * group 3: `kind` is the gap producer's own statement about whether the profile
 * backs the concept (#117 / ADR-019 / ADR-048), and putting a claimable cluster
 * under "and my vault does not cover it" would make that heading false. Reading
 * a field the producer set is not a recompute. See the WP-A report's "design
 * deviation" note — ADR-081's table lists the clusters against group 3 without
 * distinguishing their kind.
 */
function buildGroup2(inputs: ReviewInputs): ReviewItem[] {
  const { atsReport, gapClusters } = inputs;
  const terms = atsReport?.keywords.missing_claimable ?? [];
  const clusters = dedupeClusters(gapClusters ?? []).filter((g) => g.kind === "claimable");
  return [
    ...terms.map((term, i) => ({
      key: `mc-term-${i}-${normQuote(term) || term}`,
      label: term,
      kind: "term" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "warning" as const,
    })),
    ...clusters.map((g) => ({
      key: `mc-cluster-${g.id}`,
      label: g.label,
      kind: "cluster" as const,
      producers: ["clusters"] as ReviewProducer[],
      severity: "warning" as const,
      clusterId: g.id,
    })),
  ];
}

/**
 * Group 3 — honest gaps, at two granularities under one heading. Each item is
 * labelled with its origin (`Begriff` for the ATS keyword, `Cluster` for the
 * gap-analysis cluster); they are NOT fused into one list (ADR-081 cl. 2).
 */
function buildGroup3(inputs: ReviewInputs): ReviewItem[] {
  const { atsReport, gapClusters } = inputs;
  const terms = atsReport?.keywords.missing_honest_gap ?? [];
  const clusters = dedupeClusters(gapClusters ?? []).filter((g) => g.kind !== "claimable");
  return [
    ...terms.map((term, i) => ({
      key: `hg-term-${i}-${normQuote(term) || term}`,
      label: term,
      kind: "term" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "info" as const,
    })),
    ...clusters.map((g) => ({
      key: `hg-cluster-${g.id}`,
      label: g.label,
      kind: "cluster" as const,
      producers: ["clusters"] as ReviewProducer[],
      severity: "info" as const,
      clusterId: g.id,
    })),
  ];
}

/**
 * Group 4 — craft. Failing structure checks, pass-with-advisory checks,
 * `not_applicable` checks (ADR-079 cl. 4 — never silently absent) and the
 * outcome critic's advisories (ADR-060).
 *
 * Plain passing checks are NOT items: they go to `passedChecks` and collapse to
 * one line, which is the only collapse ADR-081 cl. 6 permits. That is what
 * keeps this group's count a detection signal instead of a number dominated by
 * passes (`JF-M-6.4`).
 */
function buildGroup4(inputs: ReviewInputs): { items: ReviewItem[]; passedChecks: number } {
  const { atsReport, criticReport } = inputs;
  const checks: ATSCheck[] = atsReport?.checks ?? [];
  const failed = checks.filter((c) => c.status === "fail");
  const advisory = checks.filter((c) => c.status === "pass" && c.details);
  const notApplicable = checks.filter((c) => c.status === "not_applicable");
  const passedChecks = checks.filter((c) => c.status === "pass" && !c.details).length;

  const items: ReviewItem[] = [
    ...failed.map((c) => ({
      key: `check-fail-${c.id}`,
      label: c.id,
      kind: "check" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "critical" as const,
      checkId: c.id,
      check: c,
    })),
    ...advisory.map((c) => ({
      key: `check-advisory-${c.id}`,
      label: c.id,
      kind: "check" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "info" as const,
      checkId: c.id,
      check: c,
    })),
    ...notApplicable.map((c) => ({
      key: `check-na-${c.id}`,
      label: c.id,
      kind: "check" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "neutral" as const,
      checkId: c.id,
      check: c,
    })),
  ];

  if (criticRan(criticReport)) {
    (criticReport?.advisories ?? []).forEach((a, i) => {
      items.push({
        key: `advisory-${i}-${a.concept}`,
        label: a.concept,
        kind: "advisory",
        producers: ["critic"],
        severity: "info",
        advisory: a,
      });
    });
  }

  return { items, passedChecks };
}

function unknownOf(producers: ReviewProducer[], dead: Set<ReviewProducer>): ReviewProducer[] {
  return producers.filter((p) => dead.has(p));
}

/** ADR-081 clause 2 — the four groups, in the fixed severity-and-action order. */
export function buildReviewGroups(inputs: ReviewInputs): ReviewGroup[] {
  // ADR-081 cl. 9: which producers did NOT run. `null` is the only signal the
  // frontend has for "no report persisted" — for the critic, ADR-060's
  // `ran: false` is the same statement said explicitly.
  const dead = new Set<ReviewProducer>();
  if (inputs.atsReport == null) dead.add("ats");
  if (inputs.truthReport == null) dead.add("oracle");
  if (!criticRan(inputs.criticReport)) dead.add("critic");
  if (inputs.gapClusters == null) dead.add("clusters");

  const g4 = buildGroup4(inputs);

  const clusterProducers: ReviewProducer[] =
    inputs.hasClusterProducer === false ? ["ats"] : ["ats", "clusters"];

  const specs: Array<{ id: 1 | 2 | 3 | 4; producers: ReviewProducer[]; items: ReviewItem[]; passedChecks: number }> = [
    { id: 1, producers: ["ats", "oracle"], items: buildGroup1(inputs), passedChecks: 0 },
    { id: 2, producers: clusterProducers, items: buildGroup2(inputs), passedChecks: 0 },
    { id: 3, producers: clusterProducers, items: buildGroup3(inputs), passedChecks: 0 },
    { id: 4, producers: ["ats", "critic"], items: g4.items, passedChecks: g4.passedChecks },
  ];

  return specs.map((s) => {
    const unknownProducers = unknownOf(s.producers, dead);
    return {
      id: s.id,
      items: s.items,
      producers: s.producers,
      unknownProducers,
      unknown: unknownProducers.length === s.producers.length,
      passedChecks: s.passedChecks,
    };
  });
}

/**
 * ADR-081 clause 4 — the verdict sentence's state.
 *
 * `count` is deliberately NOT computed here from the inputs: the caller passes
 * the length of the array it actually rendered, so the sentence cannot drift
 * from the list it summarises. A sentence derived independently of the list is
 * how `SF-REVIEW.3`'s double-count returns by another route.
 */
export type VerdictState =
  /** Group 1's producers did not run — the surface says so instead of claiming a clean document. */
  | { kind: "unknown" }
  /** Group 1 has findings. `count` is the number of ROWS RENDERED in group 1. */
  | { kind: "findings"; count: number }
  /** Group 1 is clean and so is everything else. */
  | { kind: "clear" }
  /**
   * Group 1 is clean but groups 2–4 are not. `JF-F-K.1`: one authoritative
   * sentence at the top of a surface is read as a summary OF the surface, so a
   * clean group 1 may not render as an unqualified all-clear while real
   * findings sit beneath it.
   */
  | { kind: "clear_with_others"; others: number };

export function verdictState(groups: ReviewGroup[], renderedGroup1Count: number): VerdictState {
  const group1 = groups.find((g) => g.id === 1);
  if (group1?.unknown) return { kind: "unknown" };
  if (renderedGroup1Count > 0) return { kind: "findings", count: renderedGroup1Count };
  const others = groups
    .filter((g) => g.id !== 1 && !g.unknown)
    .reduce((n, g) => n + g.items.length, 0);
  // ADR-081 cl. 9 read at the headline: ANY producer that did not run — not
  // only a wholly-dead group — disqualifies an unqualified all-clear. A group
  // whose ATS half ran and whose critic half did not has a real count AND a
  // real blind spot, and the sentence must not swallow the blind spot.
  const anyUnknownProducer = groups.some((g) => g.unknownProducers.length > 0);
  if (others > 0 || anyUnknownProducer) return { kind: "clear_with_others", others };
  return { kind: "clear" };
}
