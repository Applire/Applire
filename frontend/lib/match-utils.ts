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
 * Utilities for the /match job ranking page, and the gaps screen's reading of
 * a gap analysis (counts, and the ADR-089 per-cluster coverage record).
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

/**
 * A [0, 1] match score as a whole percentage — with `null` as the ONLY fallback.
 *
 * #675 / ruling B-1 (2026-09-20): a score of 0 is a real score. The backend
 * clamp no longer holds the headline up when the candidate records a denial, so
 * an analysis in which every weighted requirement is `denied` genuinely
 * publishes `match_score: 0.0`. Every read on the gaps screen was
 * `score ? Math.round(score * 100) : fallback` — a truthiness check, so a real 0
 * took the fallback branch and the screen kept showing the previous, higher
 * percentage: the same "the headline contradicts the analysis behind it" shape
 * the backend fix removes, one layer up. `null`/`undefined` (no weighted
 * requirement at all, so no score exists) is the only case that falls back.
 */
export function scoreToPercent(
  score: number | null | undefined,
  fallback: number,
): number {
  return score == null ? fallback : Math.round(score * 100);
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
 *
 * ADR-089 clause 8: the counts are the SERVER's. There used to be a second
 * argument — a client-side set of "resolved" cluster ids subtracted from the
 * category lists — which (a) held cluster ids while the lists hold concept
 * strings, so it never subtracted anything, and (b) was lost on navigation.
 * After an answer the page now replaces its whole analysis with the
 * recomputed row, whose category lists already carry the answer.
 */
export function gapCounts(gaps: GapCategories | null | undefined): GapCounts {
  const c = gaps?.category_c ?? [];
  const b = gaps?.category_b ?? [];
  return {
    directMatches: gaps?.category_a?.length ?? 0,
    likelyMatches: b.length,
    gaps: c.length,
    itemsToAddress: c.length + b.length,
  };
}

// ---------------------------------------------------------------------------
// ADR-089 — a gap cluster's persisted coverage record
// ---------------------------------------------------------------------------

/** `gap_clusters[].coverage` (ADR-089 clause 3), derived server-side from the
 * members' ledger statuses. */
export type ClusterCoverage = "open" | "partly_covered" | "covered" | "declined";

/** `gap_clusters[].outcome` (ADR-089 clause 3) — facts only, never answer text. */
export interface ClusterOutcome {
  /** Answered turns on this cluster, across every session and door. */
  asked: number;
  /** Members whose ledger row is `direct` (and not a #260 liability, ruling C-3). */
  covered: string[];
  /** Members that are recorded denials. */
  declined: string[];
  /** The sessions that asked it (the transcripts live there, not here). */
  session_ids: string[];
}

/** One persisted gap cluster, as `GET /api/job/{id}/gaps` returns it. */
export interface GapCluster {
  id: string;
  label: string;
  category: "B" | "C";
  /** OPEN members only (ADR-089 clause 3). Never the full vocabulary — use
   * {@link allClusterMembers} for that. */
  gaps: string[];
  jd_skills: string[];
  jd_context: string;
  outcome: ClusterOutcome;
  coverage: ClusterCoverage;
  /** Derived at response time: `per_gap - outcome.asked`, floored at 0
   * (ruling C-2). The same number `cluster_coverage.budget_remaining` carries
   * on a session turn. */
  budget_remaining: number;
}

/** `SessionMessageResponse.cluster_coverage` (contract item 4) — the record a
 * turn just wrote, returned on every turn that answered a cluster. */
export interface TurnClusterCoverage {
  cluster_id: string;
  coverage: ClusterCoverage;
  open_concepts: string[];
  budget_remaining: number;
}

/** `POST /api/session` 409 body codes (contract item 5). */
export type GapRefusalCode = "gap_budget_spent" | "gap_already_covered";

export function isGapRefusalCode(code: unknown): code is GapRefusalCode {
  return code === "gap_budget_spent" || code === "gap_already_covered";
}

/**
 * The ledger normaliser (`keyword_ledger._norm`: `strip().casefold()`), as
 * close as JS gets — `toLowerCase` plus the one casefold mapping German text
 * actually meets (ß → ss).
 */
export function normMember(term: string): string {
  return (term ?? "").trim().toLowerCase().replace(/ß/g, "ss");
}

/**
 * Every member of a cluster — open `gaps` + `outcome.covered` +
 * `outcome.declined`, order-preserving, deduplicated by the ledger
 * normaliser. TS mirror of `gap_coverage.all_members` (ADR-089 clause 3):
 * the ONE way to read a cluster's full term vocabulary. A reader that needs
 * the vocabulary and reads `gaps` alone fails open the moment a member is
 * covered (the #260 "tell the story" lookup is the frontend's case).
 * Tolerant of a legacy row with no `outcome`.
 */
export function allClusterMembers(
  cluster: Pick<GapCluster, "gaps"> & { outcome?: Partial<ClusterOutcome> | null },
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const lists = [cluster.gaps, cluster.outcome?.covered, cluster.outcome?.declined];
  for (const list of lists) {
    for (const term of list ?? []) {
      if (typeof term !== "string") continue;
      const key = normMember(term);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(term);
    }
  }
  return out;
}

const COVERAGE_VALUES: ReadonlySet<string> = new Set([
  "open",
  "partly_covered",
  "covered",
  "declined",
]);

/** The server's coverage, with `turn` (a follow-up turn's own report) taking
 * precedence. A legacy row that carries none reads `open`. */
export function clusterCoverage(
  cluster: Pick<GapCluster, "coverage">,
  turn?: Pick<TurnClusterCoverage, "coverage"> | null,
): ClusterCoverage {
  const value = turn?.coverage ?? cluster.coverage;
  return typeof value === "string" && COVERAGE_VALUES.has(value)
    ? (value as ClusterCoverage)
    : "open";
}

/** Remaining question budget, or `null` when the payload carries none. */
export function clusterBudgetRemaining(
  cluster: Pick<GapCluster, "budget_remaining">,
  turn?: Pick<TurnClusterCoverage, "budget_remaining"> | null,
): number | null {
  const value = turn?.budget_remaining ?? cluster.budget_remaining;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** One member chip on a card. `closed` = left the open list on a turn whose
 * covered/declined split the page has not re-read yet — shown neutrally,
 * never as covered (no false green). */
export type MemberState = "open" | "covered" | "declined" | "closed";

export interface ClusterView {
  coverage: ClusterCoverage;
  members: { term: string; state: MemberState }[];
  /** Budget left AND coverage not covered/declined (`gap_coverage.is_askable`). */
  askable: boolean;
  /** Not askable ONLY because the budget is gone — a spent budget is shown,
   * not hidden (ADR-089 clause 3). */
  budgetSpent: boolean;
  /** Answered turns on this cluster across every door. */
  asked: number;
}

/**
 * What a gap card renders — every field from the server: the persisted
 * cluster, overlaid by the turn that just answered it (`turn`, until the page
 * re-reads the analysis). No client-only state decides coverage.
 */
export function clusterView(
  cluster: GapCluster,
  turn?: TurnClusterCoverage | null,
): ClusterView {
  const coverage = clusterCoverage(cluster, turn);
  const budget = clusterBudgetRemaining(cluster, turn);
  const covered = new Set((cluster.outcome?.covered ?? []).map(normMember));
  const declined = new Set((cluster.outcome?.declined ?? []).map(normMember));
  const open = new Set((turn ? turn.open_concepts : cluster.gaps ?? []).map(normMember));
  const members = allClusterMembers(cluster).map((term) => {
    const key = normMember(term);
    const state: MemberState = open.has(key)
      ? "open"
      : declined.has(key)
        ? "declined"
        : covered.has(key)
          ? "covered"
          : "closed";
    return { term, state };
  });
  // A turn can name an open member the cluster did not carry yet (never
  // expected, but never dropped silently either).
  if (turn) {
    const known = new Set(members.map((m) => normMember(m.term)));
    for (const term of turn.open_concepts) {
      if (!known.has(normMember(term))) members.push({ term, state: "open" });
    }
  }
  const finished = coverage === "covered" || coverage === "declined";
  const hasBudget = budget === null ? true : budget > 0;
  return {
    coverage,
    members,
    askable: !finished && hasBudget,
    budgetSpent: !finished && !hasBudget,
    asked: cluster.outcome?.asked ?? 0,
  };
}

/** Cluster tallies for the page's badges and headings — server data only. */
export function clusterCoverageCounts(clusters: GapCluster[] | null | undefined): {
  covered: number;
  askable: number;
} {
  let covered = 0;
  let askable = 0;
  for (const c of clusters ?? []) {
    const v = clusterView(c);
    if (v.coverage === "covered") covered += 1;
    if (v.askable) askable += 1;
  }
  return { covered, askable };
}
