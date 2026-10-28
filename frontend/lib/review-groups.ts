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
 * | 3 | What is missing, and my vault does not cover it?      | the job's open requirements (R2-10, F1-2) — legacy: `keywords.missing_honest_gap` + clusters |
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
import type {
  CriticAdvisory,
  CrossDocumentItem,
  OutcomeCriticReport,
} from "@/components/cv/CriticAdvisoryPanel";
import {
  baseId,
  isSilentPass,
  usesLocalizedDetail,
  type ATSCheck,
  type ATSReport,
  type CheckSignal,
  type DeliveryClass,
  type DeliveryRemoval,
} from "./ats-report";
import type { ReviewAction, ReviewDecision, ReviewState } from "./api/document-review";
import type { LocateTarget } from "./locate-in-preview";
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
export type ReviewItemKind = "term" | "cluster" | "claim" | "check" | "advisory" | "signal";

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
  /** ADR-052 amended 2026-10-22: the Oracle detail's catalog key + params, carried verbatim. */
  detailKey?: string | null;
  detailParams?: Record<string, string> | null;
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
  /**
   * #703 (RULING R-3 = A): the terminal review's repeated-demand signal for this
   * term — on a group-2 row when it is still `open`, as its own group-4 `signal`
   * row when it `landed` late. Carried verbatim from `terminal-review.signals`.
   */
  signal?: CheckSignal;
  /** For a claim row: where in the document the Oracle found it. */
  location?: string | null;
  /**
   * Group 1 only — ADR-090 cl. 6's `finding_key`: `"<producer>:<normQuote(text)>"`,
   * no list index. The key the review endpoints take (Contract 2).
   */
  findingKey?: string;
  /**
   * Group 1 only — what *Show me where* looks for in the preview (ADR-090
   * cl. 2). `null` = the report carries no matched forms for this finding: the
   * card says the place could not be marked.
   */
  targets?: LocateTarget[] | null;
  /**
   * Group 1 term rows only — build-2 delivery F2 (SF-REVIEW.18): the candidate's
   * own recorded denials that cover this term (`keywords.present_unsupported_denials`).
   * Non-empty → the card shows them and offers no add-to-profile handle.
   */
  denials?: string[];
  /**
   * #803 belt (T-2 = B): the term occurs only in sentences naming the employer.
   * The row stays (take-out + edit) but is neutral and never counted.
   */
  employerSentence?: boolean;
  /**
   * ADR-099 cl. 2 — the row kind's delivery class and resolver, READ from the
   * producer's row (check / Oracle verdict / keyword bucket). `null` or absent
   * = unclassified: rendered as today's fallback, never hidden.
   */
  deliveryClass?: DeliveryClass | null;
  resolver?: string | null;
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
  /**
   * R2-10 (ruling F1-2, lead G hunk): the job's OPEN requirements — the gap
   * row's ledger rows with `fit_weight > 0` and status `gap`
   * (`openRequirementConcepts`), the same set the gaps page counts. When
   * given, group 3 IS this list: one number on every surface. The ATS
   * `missing_honest_gap` list is a different population (posting keywords,
   * mostly `fit_weight 0` — round 2: 23 here vs 13 on the gaps page, 6 vs 0)
   * and stays on the ATS panel. `undefined` = not supplied (legacy shape);
   * `null` = the gap row did not load → group 3 renders *unknown* (cl. 9).
   */
  openRequirements?: string[] | null;
}

/** #702: the advisory kinds the cross-document section owns. */
const CROSS_DOCUMENT_KINDS = new Set(["letter_only", "letter_richer"]);

/** ADR-060: the outcome critic is an EXCEPTION surface — absent or `ran: false` both mean it did not run. */
/**
 * ADR-081 cl. 9 / adv-review finding 10 (2026-10-07): the critic ANSWERED only
 * when it ran and its judgement call did not error. The backend persists
 * `ran: true, reason: "judgement_error", advisories: []` on a failed call — read
 * as "ran", that is "nothing open", the exact silence cl. 9 forbids.
 */
export function criticAnswered(report: OutcomeCriticReport): boolean {
  return Boolean(report && report.ran && report.reason !== "judgement_error");
}

const criticRan = criticAnswered;

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
  const matches = atsReport?.keywords.present_unsupported_matches;
  const denialsByTerm = atsReport?.keywords.present_unsupported_denials;
  const employerSentence = new Set(atsReport?.keywords.present_unsupported_employer_sentence ?? []);
  const claimable = claimableConceptSet(atsReport?.keywords.claimable_concepts);
  const claims = truthReport ? flaggedClaims(truthReport.claims ?? [], claimable) : [];
  const buckets = atsReport?.keywords.delivery ?? null;

  const claimsByNorm = new Map<string, number>();
  claims.forEach((c, i) => {
    const n = normQuote(c.claim.text);
    if (!claimsByNorm.has(n)) claimsByNorm.set(n, i);
  });

  const consumedClaims = new Set<number>();
  const seenKeys = new Set<string>();
  const items: ReviewItem[] = [];

  terms.forEach((term) => {
    const n = normQuote(term);
    // ADR-090 cl. 6: the key is producer + fold, WITHOUT the list index — an
    // index shifts when an earlier finding clears. Two terms that fold equal
    // are one finding (ADR-081 cl. 2's own reading).
    const findingKey = `ats:${n || term}`;
    if (seenKeys.has(findingKey)) return;
    seenKeys.add(findingKey);
    const claimIndex = claimsByNorm.get(n);
    const overlap = claimIndex !== undefined && !consumedClaims.has(claimIndex);
    if (overlap) consumedClaims.add(claimIndex);
    const claim = overlap ? claims[claimIndex] : null;
    // Contract 3: an absent key means "no data" → the card says the place
    // could not be marked. A merged row can still be located by the claim text
    // (ADR-090 cl. 2: an Oracle claim is located by its `text`).
    const termMatches = matches && Object.prototype.hasOwnProperty.call(matches, term) ? matches[term] : null;
    const targets: LocateTarget[] | null = termMatches
      ? termMatches.map((m) => ({ form: m.form, stem: Boolean(m.stem) }))
      : claim
        ? [{ form: claim.claim.text }]
        : null;
    // ADR-099: a merged row takes the Oracle verdict's class (it names the
    // kind: misattributed, unbacked …); a term row its keyword bucket's — the
    // narrower bucket first (employer sentence, covered by a denial).
    const termDenials =
      denialsByTerm && Object.prototype.hasOwnProperty.call(denialsByTerm, term) ? denialsByTerm[term] : [];
    const bucket = buckets
      ? employerSentence.has(term)
        ? buckets.present_unsupported_employer_sentence
        : termDenials.length > 0
          ? (buckets.present_denied ?? buckets.present_unsupported)
          : buckets.present_unsupported
      : undefined;
    const claimDelivery = claim?.verdict.delivery_class != null ? claim.verdict : null;
    items.push({
      key: findingKey,
      findingKey,
      label: term,
      kind: "term",
      deliveryClass: claimDelivery ? claimDelivery.delivery_class : (bucket?.delivery_class ?? null),
      resolver: claimDelivery ? (claimDelivery.resolver ?? null) : (bucket?.resolver ?? null),
      producers: overlap ? ["ats", "oracle"] : ["ats"],
      severity: employerSentence.has(term) && !overlap ? "neutral" : "critical",
      employerSentence: employerSentence.has(term) && !overlap,
      detail: claim?.verdict.detail ?? null,
      detailKey: claim?.verdict.detail_key ?? null,
      detailParams: claim?.verdict.detail_params ?? null,
      location: claim?.claim.location ?? null,
      targets,
      denials:
        denialsByTerm && Object.prototype.hasOwnProperty.call(denialsByTerm, term)
          ? denialsByTerm[term].filter((d) => typeof d === "string" && d.length > 0)
          : [],
    });
  });

  claims.forEach((c, i) => {
    if (consumedClaims.has(i)) return;
    const findingKey = `oracle:${normQuote(c.claim.text) || c.claim.text}`;
    if (seenKeys.has(findingKey)) return;
    seenKeys.add(findingKey);
    items.push({
      key: findingKey,
      findingKey,
      label: c.claim.text,
      kind: "claim",
      deliveryClass: c.verdict.delivery_class ?? null,
      resolver: c.verdict.resolver ?? null,
      producers: ["oracle"],
      severity: "critical",
      detail: c.verdict.detail ?? null,
      detailKey: c.verdict.detail_key ?? null,
      detailParams: c.verdict.detail_params ?? null,
      location: c.claim.location,
      targets: [{ form: c.claim.text }],
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
/**
 * #703 — the `terminal-review` check's repeated-demand signals (RULING R-3 = A,
 * threshold ≥ 2 rounds, applied by the backend). Read, never recomputed.
 */
export function repeatedDemandSignals(atsReport: ATSReport): CheckSignal[] {
  const check = (atsReport?.checks ?? []).find((c) => c.id === "terminal-review");
  return (check?.signals ?? []).filter((s) => s.kind === "repeated_demand");
}

function buildGroup2(inputs: ReviewInputs): ReviewItem[] {
  const { atsReport, gapClusters } = inputs;
  const terms = atsReport?.keywords.missing_claimable ?? [];
  const clusters = dedupeClusters(gapClusters ?? []).filter((g) => g.kind === "claimable");
  // #703: an OPEN repeated demand annotates the group-2 row it belongs to. The
  // LIVE report decides whether the term is still missing — a term the user has
  // since added is no longer a group-2 row, so its (frozen) signal shows nowhere
  // here. An open term the ATS list names differently gets its own row: it IS
  // "missing although your profile covers it".
  const open = repeatedDemandSignals(atsReport).filter((s) => s.weight === "open");
  const byNorm = new Map(open.map((s) => [normQuote(s.term), s]));
  const used = new Set<string>();
  const termRows: ReviewItem[] = terms.map((term, i) => {
    const n = normQuote(term);
    const signal = byNorm.get(n);
    if (signal) used.add(n);
    return {
      key: `mc-term-${i}-${n || term}`,
      label: term,
      kind: "term" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "warning" as const,
      ...(signal ? { signal } : {}),
    };
  });
  const extraRows: ReviewItem[] = atsReport
    ? open
        .filter((s) => !used.has(normQuote(s.term)))
        .map((s) => ({
          key: `mc-signal-${normQuote(s.term) || s.term}`,
          label: s.term,
          kind: "term" as const,
          producers: ["ats"] as ReviewProducer[],
          severity: "warning" as const,
          signal: s,
        }))
    : [];
  // Annotated rows first: the weighted ones are what the candidate reads first.
  const ordered = [...termRows.filter((r) => r.signal), ...extraRows, ...termRows.filter((r) => !r.signal)];
  return [
    ...ordered,
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
  const { atsReport, gapClusters, openRequirements } = inputs;
  if (openRequirements !== undefined) {
    // R2-10: the open requirements, verbatim from the gap row (F1-2).
    return (openRequirements ?? []).map((concept) => ({
      key: `hg-req-${normQuote(concept) || concept}`,
      label: concept,
      kind: "term" as const,
      producers: ["clusters"] as ReviewProducer[],
      severity: "info" as const,
    }));
  }
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
 * #759 (ADR-081 amended 2026-10-08) — the checks whose FAILURE does stand in the
 * way of sending. A fixed list of the producer's own check ids (this module reads,
 * it does not compute): a failed `document-language` check means the document
 * mixes languages, and it may not sit under "Nichts davon steht dem Versand im
 * Weg". Such a row leaves group 4 and renders in its own block above it.
 */
export const SEND_CHECK_IDS: ReadonlySet<string> = new Set(["document-language"]);

const isSendCheck = (c: ATSCheck): boolean => c.status === "fail" && SEND_CHECK_IDS.has(baseId(c.id));

/** #759 — the failed send-blocking checks, as rows for the "before you send it" block. */
export function buildSendChecks(atsReport: ATSReport): ReviewItem[] {
  return (atsReport?.checks ?? []).filter(isSendCheck).map((c) => ({
    key: `check-send-${c.id}`,
    label: c.id,
    kind: "check" as const,
    producers: ["ats"] as ReviewProducer[],
    severity: "warning" as const,
    checkId: c.id,
    check: c,
    deliveryClass: c.delivery_class ?? null,
    resolver: c.resolver ?? null,
  }));
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
  // #759: a failed send-blocking check is NOT craft — see `buildSendChecks`.
  const failed = checks.filter((c) => c.status === "fail" && !isSendCheck(c));
  // R2-5: a clean terminal-review pass is counted as passed, never an advisory row.
  const advisory = checks.filter((c) => c.status === "pass" && !isSilentPass(c));
  const notApplicable = checks.filter((c) => c.status === "not_applicable");
  const passedChecks = checks.filter((c) => isSilentPass(c)).length;

  const items: ReviewItem[] = [
    ...failed.map((c) => ({
      key: `check-fail-${c.id}`,
      label: c.id,
      kind: "check" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "critical" as const,
      checkId: c.id,
      check: c,
      deliveryClass: c.delivery_class ?? null,
      resolver: c.resolver ?? null,
    })),
    ...advisory.map((c) => ({
      key: `check-advisory-${c.id}`,
      label: c.id,
      kind: "check" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "info" as const,
      checkId: c.id,
      check: c,
      deliveryClass: c.delivery_class ?? null,
      resolver: c.resolver ?? null,
    })),
    ...notApplicable.map((c) => ({
      key: `check-na-${c.id}`,
      label: c.id,
      kind: "check" as const,
      producers: ["ats"] as ReviewProducer[],
      severity: "neutral" as const,
      checkId: c.id,
      check: c,
      deliveryClass: c.delivery_class ?? null,
      resolver: c.resolver ?? null,
    })),
  ];

  // #703: a demand that LANDED late — re-read the sentence it landed in.
  repeatedDemandSignals(atsReport)
    .filter((s) => s.weight === "landed")
    .forEach((s) => {
      items.push({
        key: `signal-${normQuote(s.term) || s.term}`,
        label: s.term,
        kind: "signal",
        producers: ["ats"],
        severity: "info",
        signal: s,
      });
    });

  if (criticRan(criticReport)) {
    // #702 (ADR-060 amended 2026-10-07): when the backend derives the
    // cross-document items, the letter_only / letter_richer advisories move to
    // the cross-document section and leave this group. An older response
    // without the field keeps every advisory here (no row is ever dropped).
    const crossDocumentDerived = Array.isArray(criticReport?.cross_document);
    (criticReport?.advisories ?? []).forEach((a, i) => {
      if (crossDocumentDerived && CROSS_DOCUMENT_KINDS.has(a.kind)) return;
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
    {
      id: 3,
      // R2-10: with the open requirements supplied, the gap row is group 3's
      // only producer — its liveness is `openRequirements !== null`.
      producers: inputs.openRequirements !== undefined ? ["clusters"] : clusterProducers,
      items: buildGroup3(inputs),
      passedChecks: 0,
    },
    { id: 4, producers: ["ats", "critic"], items: g4.items, passedChecks: g4.passedChecks },
  ];

  return specs.map((s) => {
    const unknownProducers =
      s.id === 3 && inputs.openRequirements !== undefined
        ? inputs.openRequirements === null
          ? (["clusters"] as ReviewProducer[])
          : []
        : unknownOf(s.producers, dead);
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
  | {
      kind: "clear_with_others";
      others: number;
      /**
       * T-1 (ADR-081 cl. 4 amended 2026-10-22): of `others`, the rows that ask
       * for a decision before sending (`decisionCount`) and the rest (`notes`).
       * UX audit 2026-10-20 (Felix, Emma): an all-clear sentence on top of 32
       * findings read as "done" — the headline now names what needs deciding.
       */
      decisions: number;
      notes: number;
    }
  /**
   * #789 line L60 (ADR-081 cl. 9 at the headline): group 1 is clean, but at
   * least one producer of the surface did not answer (`unchecked`, de-duplicated,
   * in group order). The sentence names the blind check instead of claiming
   * that "findings below do not stand in the way" — with `others = 0` there are
   * none, and with `others > 0` the blind spot would be swallowed.
   */
  | { kind: "clear_unchecked"; others: number; decisions: number; notes: number; unchecked: ReviewProducer[] }
  /**
   * #759 — group 1 is clean, but a send-blocking check failed (`buildSendChecks`).
   * The sentence names it instead of calling the rest harmless.
   */
  | { kind: "clear_with_send_checks"; count: number };

export function verdictState(
  groups: ReviewGroup[],
  renderedGroup1Count: number,
  sendCheckCount = 0,
): VerdictState {
  const group1 = groups.find((g) => g.id === 1);
  if (group1?.unknown) return { kind: "unknown" };
  if (renderedGroup1Count > 0) return { kind: "findings", count: renderedGroup1Count };
  if (sendCheckCount > 0) return { kind: "clear_with_send_checks", count: sendCheckCount };
  const others = groups
    .filter((g) => g.id !== 1 && !g.unknown)
    .reduce((n, g) => n + g.items.length, 0);
  // ADR-081 cl. 9 read at the headline: ANY producer that did not run — not
  // only a wholly-dead group — disqualifies an unqualified all-clear. A group
  // whose ATS half ran and whose critic half did not has a real count AND a
  // real blind spot, and the sentence must not swallow the blind spot.
  const unchecked: ReviewProducer[] = [];
  for (const g of groups) for (const p of g.unknownProducers) if (!unchecked.includes(p)) unchecked.push(p);
  const decisions = decisionCount(groups);
  const notes = others - decisions;
  if (unchecked.length > 0) return { kind: "clear_unchecked", others, decisions, notes, unchecked };
  if (others > 0) return { kind: "clear_with_others", others, decisions, notes };
  return { kind: "clear" };
}

/**
 * T-1 (ADR-081 cl. 4 amended 2026-10-22): the rows BELOW group 1 that ask the
 * candidate to decide something before sending — every group-2 row (a keyword
 * the vault covers and the document leaves out: add it or not) and every
 * FAILED craft check of group 4. Group 3 (honest gaps), passing-with-detail and
 * not-applicable checks, critic advisories and late-landed signals are notes:
 * worth knowing, nothing to decide. An unknown group contributes nothing
 * (ADR-081 cl. 9 — never a number for a producer that did not run).
 *
 * The phone review button carries this number when group 1 is clean.
 */
export function decisionCount(groups: ReviewGroup[]): number {
  return groups.reduce((n, g) => n + groupDecisionCount(g), 0);
}

/** Whether a row of `group` is one the T-1 headline counts as a decision. */
export function isDecisionItem(group: ReviewGroup, item: ReviewItem): boolean {
  if (group.id === 2) return true;
  if (group.id === 4) return item.kind === "check" && item.severity === "critical";
  return false;
}

/**
 * One group's share of the headline's decision count — the SAME predicate, so
 * the panel can mark on each group row exactly what the headline counts
 * (Hazelnut build 3 re-probe N1: "2 points need your decision" over a panel
 * that marked none of them).
 */
export function groupDecisionCount(group: ReviewGroup): number {
  if (group.unknown) return 0;
  return group.items.filter((it) => isDecisionItem(group, it)).length;
}

/* ---------------------------------------------------------- ADR-090 cl. 6 */

/** A group-1 row: an open finding, or a decision whose finding the report no longer lists. */
export interface Group1Row {
  findingKey: string;
  label: string;
  /** `open` whenever the CURRENT report lists the finding — whatever the state says. */
  status: "open" | ReviewAction;
  /** The report's row; `null` for a decided row (its finding is gone). */
  item: ReviewItem | null;
  /** The latest decision on this finding, if any. */
  decision: ReviewDecision | null;
}

/** The fold part of a finding key — a merged row may be recorded under either producer. */
function keyFold(findingKey: string): string {
  const i = findingKey.indexOf(":");
  return i >= 0 ? findingKey.slice(i + 1) : findingKey;
}

/**
 * #797 (ruling D2-5): the number of OPEN group-1 rows — the tab badge and the
 * save receipt count this, not the listed items, because a `supported`
 * decision closes a finding the report still lists.
 */
export function openGroup1Count(items: ReviewItem[], state: ReviewState | null | undefined): number {
  return countedOpenRows(buildGroup1Rows(items, state)).length;
}

/**
 * The open rows the headline and the badges count — #803 belt (T-2 = B): an
 * employer-sentence row is listed and walkable, but never counted.
 */
export function countedOpenRows(rows: Group1Row[]): Group1Row[] {
  // ADR-099 cl. 6: a CLASSIFIED employer-sentence row is class-A residue
  // ("Entfernen") and counts; the legacy unclassified row stays uncounted.
  return rows.filter(
    (r) => r.status === "open" && (!r.item?.employerSentence || r.item.deliveryClass === "A"),
  );
}

/**
 * ADR-090 cl. 6 — every count is derived from the LIVE report. A finding the
 * report lists is open, whatever `review_state` says; a decision only LABELS a
 * finding the report no longer lists. A decision whose finding reappears is
 * open again. Decided rows come first (in decision order), then the open ones
 * in the report's order.
 */
export function buildGroup1Rows(items: ReviewItem[], state: ReviewState | null | undefined): Group1Row[] {
  const latest = new Map<string, ReviewDecision>();
  for (const d of state?.decisions ?? []) {
    // #702: a cross-document decision (`critic:` key) is never a group-1 row;
    // ADR-099 cl. 9: nor the translate decision (`send:` key).
    if (d.finding_key.startsWith("critic:") || d.finding_key.startsWith("send:") || d.finding_key.startsWith("page:")) continue;
    latest.set(keyFold(d.finding_key), d);
  }

  const listedFolds = new Set(items.map((it) => keyFold(it.findingKey ?? it.key)));
  const decided: Group1Row[] = [];
  latest.forEach((d, fold) => {
    if (listedFolds.has(fold)) return;
    // ADR-099 cl. 6: `no_change` is an OPEN marker ("the answer changed
    // nothing"), never a closing label for a finding that left the report.
    if (d.action === "no_change") return;
    decided.push({ findingKey: d.finding_key, label: d.label, status: d.action, item: null, decision: d });
  });
  // ADR-099 cl. 6 retired #797's label closure (ruling D2-5): a finding the
  // live report lists is OPEN whatever was decided on it — "Stimmt" without a
  // vault change no longer closes a row.
  decided.sort((a, b) => (a.decision!.at < b.decision!.at ? -1 : a.decision!.at > b.decision!.at ? 1 : 0));

  const open: Group1Row[] = items.map((it) => {
    const key = it.findingKey ?? it.key;
    return { findingKey: key, label: it.label, status: "open", item: it, decision: latest.get(keyFold(key)) ?? null };
  });
  return [...decided, ...open];
}

/* ------------------------------------------------- #702 cross-document */

/** One cross-document card/row: the item, and the decision recorded on it. */
export interface CrossDocumentRow {
  item: CrossDocumentItem;
  /**
   * The critic does not re-run after an edit, so a recorded decision LABELS the
   * item decided (unlike group 1, where the live report decides — ADR-090 cl. 6
   * amended 2026-10-07).
   */
  status: "open" | "kept" | "taken_out" | "edited";
  decision: ReviewDecision | null;
}

export interface CrossDocumentSectionState {
  /** ADR-081 cl. 9: the critic did not run → *unknown*, never an empty section. */
  unknown: boolean;
  rows: CrossDocumentRow[];
  decided: number;
}

/**
 * #702 — the cross-document section's rows: the backend's derived items in its
 * order (high weight first), each with the latest `critic:` decision on it.
 * An older backend response without `cross_document` yields no rows (its
 * advisories stay in group 4, see `buildGroup4`).
 */
export function buildCrossDocument(
  criticReport: OutcomeCriticReport,
  state: ReviewState | null | undefined,
): CrossDocumentSectionState {
  if (!criticRan(criticReport)) return { unknown: true, rows: [], decided: 0 };
  const latest = new Map<string, ReviewDecision>();
  for (const d of state?.decisions ?? []) {
    if (d.finding_key.startsWith("critic:")) latest.set(keyFold(d.finding_key), d);
  }
  const rows: CrossDocumentRow[] = (criticReport?.cross_document ?? []).map((item) => {
    const decision = latest.get(keyFold(item.key)) ?? null;
    const status =
      decision && (decision.action === "kept" || decision.action === "taken_out" || decision.action === "edited")
        ? decision.action
        : "open";
    return { item, status, decision };
  });
  return { unknown: false, rows, decided: rows.filter((r) => r.status !== "open").length };
}

/* ------------------------------------- B3-10 — "Aus Deiner Eingabe im Formular" */

/** The Oracle checker of a letter sentence built from the letter form's input. */
export const LETTER_FORM_CHECKER = "letter_form_input";

/** One neutral row: a sentence the candidate's own form input backs. */
export interface FormInputRow {
  key: string;
  text: string;
  detail: string | null;
  detailKey: string | null;
  detailParams: Record<string, string> | null;
}

/**
 * Ruling B3-10 / RULING FO-1 = A (2026-10-23): a letter sentence the Oracle marks
 * `letter_form_input` (`not_applicable`) is the candidate's own statement for
 * this letter. It gets a small neutral section below the group-1 list — no
 * buttons, and it is NOT a group-1 item: never in the headline, never in the
 * "k of n decided" bar (group 1 reads only `FLAG_VERDICTS`). One row per
 * sentence (`normQuote` fold — a clause split repeats the sentence text).
 */
export function buildFormInputRows(truthReport: TruthfulnessReport): FormInputRow[] {
  const rows: FormInputRow[] = [];
  const seen = new Set<string>();
  for (const c of truthReport?.claims ?? []) {
    if (c.verdict.checker !== LETTER_FORM_CHECKER) continue;
    const key = `form:${normQuote(c.claim.text) || c.claim.text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      key,
      text: c.claim.text,
      detail: c.verdict.detail ?? null,
      detailKey: c.verdict.detail_key ?? null,
      detailParams: c.verdict.detail_params ?? null,
    });
  }
  return rows;
}

/* ------------------------------------- ADR-099 — fix it, ask once, or stay silent */

/**
 * The resolvers whose row is a class-U "did not run" row (ADR-099 cl. 2/9): a
 * class-F row whose loop did not run clean falls back to U when its registry
 * resolver is one of these, to A otherwise.
 */
const U_RESOLVERS: ReadonlySet<string> = new Set(["recheck", "regenerate"]);

/** The group-1 question resolvers the card knows (ADR-099 cl. 6). */
export const QUESTION_RESOLVERS: ReadonlySet<string> = new Set([
  "ask_station",
  "ask_true",
  "ask_achieved",
  "remove_only",
]);

/** How a row renders, after its class is read (never computed) — ADR-099 cl. 2/4. */
export type EffectiveClass = "A" | "U" | "hidden" | "unclassified";

/**
 * ADR-099 cl. 4(d)/(e): F is hidden ONLY when the loop ran clean
 * (`delivery.loop_clean === true`); otherwise it renders as its fallback (U for
 * a re-run resolver, A otherwise). S is hidden — except while the writing loop
 * degraded (T-S: an S kind never hides when `review_degradation` is non-empty),
 * then it renders as an unclassified note. `null` = unclassified, never S.
 */
export function effectiveClass(
  cls: DeliveryClass | null | undefined,
  resolver: string | null | undefined,
  loopClean: boolean | null | undefined,
  degraded: boolean,
): EffectiveClass {
  if (cls == null) return "unclassified";
  if (cls === "F") {
    if (loopClean === true) return "hidden";
    return resolver && U_RESOLVERS.has(resolver) ? "U" : "A";
  }
  if (cls === "S") return degraded ? "unclassified" : "hidden";
  return cls;
}

/** One class-U row: a producer that did not answer, or a check that did not run. */
export interface UnknownRow {
  key: string;
  /** `recheck` → "Jetzt prüfen" (full report re-run); `regenerate` → "Neue Fassung erstellen". */
  resolver: "recheck" | "regenerate";
  /** A producer of the surface that did not run (`ats` / `oracle`). */
  producer?: ReviewProducer;
  /** A check whose own class is U (e.g. `terminal-review` not run). */
  check?: ATSCheck;
}

export interface DeliveryView {
  /**
   * False when NO row of the reports carries a delivery class (a report
   * persisted before ADR-099): every row renders as its unclassified fallback —
   * "None = unclassified, never hidden". "Jetzt prüfen" re-audits and classifies.
   */
  classified: boolean;
  /**
   * Group-1 rows rendered as ONE-question cards (`row.item.resolver` is the
   * question; `null` = unclassified → today's handles). Decided rows (the
   * finding left the report) stay listed with their label, uncounted.
   */
  questions: Group1Row[];
  /** Class-A send checks — today the failed `document-language` check ("Diese Stellen übersetzen"). */
  translate: ReviewItem[];
  /**
   * The LETTER's page-length card (INTERFACE B-2: the `page-length` check,
   * class A, resolver `page_length`, params `{pages, letterPages}`). The CV's
   * card renders from `page_overflow`, never from a row — the page passes it.
   */
  pageLength: ReviewItem | null;
  /** Class-U rows ("Nicht geprüft … Jetzt prüfen" / "Neue Fassung erstellen"). */
  unknown: UnknownRow[];
  /**
   * Rows of a classified report whose own row is unclassified (or class S while
   * the loop degraded) — rendered as today's row, without server prose.
   * `counted` marks the ones today's predicate counts (a failed check).
   */
  fallback: Array<{ item: ReviewItem; counted: boolean }>;
  /** "N Prüfungen bestanden" — passed checks + class-F rows the loop handled (cl. 3). */
  passed: number;
  /** ADR-099 cl. 5 — the collapsed, uncounted removal line. */
  removals: DeliveryRemoval[];
  /** Headline + phone badge: the class A and U rows of THIS view (cl. 3), before page extras. */
  count: number;
}

export interface DeliveryInputs extends ReviewInputs {
  /** `review_degradation` from the status door — non-empty = the loop degraded. */
  degradation?: unknown[] | null;
  /** ADR-090 cl. 6 — the persisted per-document decisions. */
  reviewState?: ReviewState | null;
  /**
   * Counted cards the PAGE renders into the surface (page length, unasked
   * requirements, the letter's signature) — ONE number for the headline, the
   * tab badge and the phone badge (ADR-099 cl. 3).
   */
  extraCounted?: number;
  /**
   * The page renders the page-length card itself (the CV's PageOverflowCard,
   * counted through `extraCounted`). Then the report's `page-length` row is
   * neither a second card nor a second count — page length has ONE place
   * (ADR-099 cl. 7). The letter's card is the surface's own (false).
   */
  pageCardFromPage?: boolean;
}

/** Does any row of the reports carry an ADR-099 delivery class? */
export function reportIsClassified(atsReport: ATSReport, truthReport: TruthfulnessReport): boolean {
  if (atsReport?.delivery) return true;
  if (atsReport?.keywords.delivery) return true;
  if ((atsReport?.checks ?? []).some((c) => c.delivery_class != null)) return true;
  return (truthReport?.claims ?? []).some((c) => c.verdict.delivery_class != null);
}

/**
 * ADR-099 — the review surface's rows by delivery class. A pure read of the
 * producers' rows: the class and the resolver come from the row (WP-B's
 * registry annotates them); nothing here classifies a kind.
 *
 * Removed from the surface regardless of class (ADR-099 cl. 7/8, UAT-3b/c/j):
 * group 2 (missing-but-covered terms, claimable clusters, repeated-demand
 * signals), the cross-document section and the critic advisories — the
 * structured report keeps them for agents (ADR-077 cl. 5).
 */
export function buildDeliveryView(inputs: DeliveryInputs): DeliveryView {
  const { atsReport, truthReport } = inputs;
  const loopClean = atsReport?.delivery?.loop_clean ?? null;
  const degraded = Array.isArray(inputs.degradation) && inputs.degradation.length > 0;
  const groups = buildReviewGroups(inputs);
  const classified = reportIsClassified(atsReport, truthReport);

  const questionItems: ReviewItem[] = [];
  const translate: ReviewItem[] = [];
  const unknown: UnknownRow[] = [];
  let pageLength: ReviewItem | null = null;
  const fallback: Array<{ item: ReviewItem; counted: boolean }> = [];
  let hiddenF = 0;

  // Producer liveness (ADR-081 cl. 9 stands; ADR-099 cl. 2 U, T-U): the ATS
  // report or the Oracle did not run → one "Nicht geprüft … Jetzt prüfen" row
  // each. The outcome critic answering `ran:false` / `judgement_error` is U as
  // well (cl. 2 names `ran:false`); it runs only inside the writing loop now
  // (cl. 8), so its one action is a new version, not a re-check.
  if (atsReport == null) unknown.push({ key: "producer-ats", resolver: "recheck", producer: "ats" });
  if (truthReport == null || (truthReport.judgement_unavailable ?? 0) > 0) {
    unknown.push({ key: "producer-oracle", resolver: "recheck", producer: "oracle" });
  }
  const critic = inputs.criticReport;
  if (critic && (critic.ran === false || critic.reason === "judgement_error")) {
    unknown.push({ key: "producer-critic", resolver: "regenerate", producer: "critic" });
  }

  // Group 1 — one question per kind.
  for (const item of groups.find((g) => g.id === 1)!.items) {
    const eff = effectiveClass(item.deliveryClass, item.resolver, loopClean, degraded);
    if (eff === "hidden") {
      hiddenF += item.deliveryClass === "F" ? 1 : 0;
      continue;
    }
    if (eff === "U") {
      unknown.push({ key: `g1-${item.key}`, resolver: (item.resolver as "recheck" | "regenerate") ?? "recheck" });
      continue;
    }
    // A (or unclassified: the card falls back to today's handles).
    questionItems.push(eff === "A" ? item : { ...item, resolver: null });
  }
  const questions = buildGroup1Rows(questionItems, inputs.reviewState);

  // Checks — translation, U rows, unclassified fallback, passes. Page length
  // is never a ROW: its one place is the card (cl. 7) — the letter's from its
  // class-A check, the CV's from `page_overflow` (the page, via `extraCounted`).
  const checks: ATSCheck[] = atsReport?.checks ?? [];
  let passed = 0;
  for (const c of checks) {
    const item: ReviewItem = {
      key: `check-${c.status}-${c.id}`,
      label: c.id,
      kind: "check",
      producers: ["ats"],
      severity: c.status === "fail" ? "critical" : c.status === "not_applicable" ? "neutral" : "info",
      checkId: c.id,
      check: c,
      deliveryClass: c.delivery_class ?? null,
      resolver: c.resolver ?? null,
    };
    // ADR-099 cl. 7 / 10: page length has ONE place — the page's card.
    if (baseId(c.id) === "page-length" || c.id === "page-length-kept") {
      if (c.status === "pass") passed += 1;
      else if (c.delivery_class === "A" && c.resolver === "page_length" && !pageLength) pageLength = item;
      continue;
    }
    if (c.status === "not_applicable") continue;
    if (c.status === "pass" && c.delivery_class !== "U") {
      passed += 1;
      continue;
    }
    const eff = effectiveClass(c.delivery_class, c.resolver, loopClean, degraded);
    if (eff === "hidden") {
      if (c.delivery_class === "F") hiddenF += 1;
      continue;
    }
    if (eff === "U") {
      unknown.push({
        key: `check-${c.id}`,
        resolver: c.resolver === "regenerate" ? "regenerate" : "recheck",
        check: c,
      });
      continue;
    }
    if (eff === "A" && c.resolver === "translate") {
      translate.push(item);
      continue;
    }
    // A failed check that is unclassified (a legacy report, or a kind the
    // registry does not know) or class A without a dedicated card: today's row,
    // counted — never hidden (cl. 2: None is never S). cl. 12: no server prose.
    fallback.push({ item, counted: true });
  }

  const removals = atsReport?.delivery?.removals ?? [];
  const count =
    countedOpenRows(questions).length +
    translate.length +
    (pageLength && !inputs.pageCardFromPage ? 1 : 0) +
    unknown.length +
    fallback.filter((f) => f.counted).length +
    (degraded ? 1 : 0) +
    (inputs.extraCounted ?? 0);

  return {
    classified,
    questions,
    translate,
    pageLength,
    unknown,
    fallback,
    // B-2: B never stamps "F" on a row; the loop's fixed kinds arrive as
    // `delivery.fixed`. `hiddenF` stays for a row a future producer stamps F.
    passed: passed + hiddenF + (atsReport?.delivery?.fixed?.length ?? 0),
    removals,
    count,
  };
}

/** True when an unkeyed check would render server prose (ADR-099 cl. 12) — the surface shows only the label then. */
export function hasKeyedDetail(c: ATSCheck): boolean {
  return usesLocalizedDetail(c);
}

/**
 * ADR-099 cl. 6 — the end state of a "Stimmt das?" card: the latest decision
 * on a still-listed finding says the answer did not clear it (`no_change`, or
 * `added` while the live report still lists it) → only "Entfernen" remains.
 */
export function removeOnly(row: Group1Row): boolean {
  return row.status === "open" && (row.decision?.action === "no_change" || row.decision?.action === "added");
}
