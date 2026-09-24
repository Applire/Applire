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

/** One member's ledger fact (ruling C-1 contract addition): `covered` =
 * `direct` and not an unstoried #260 liability; `partial` = `partial` or an
 * unstoried liability (ruling A-1); `gap` = `gap` or no matching ledger row;
 * `declined` = a recorded denial (checked first). */
export type MemberStatus = "covered" | "partial" | "gap" | "declined";

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
  /** Derived at response time over ALL members (ruling C-1): each member's
   * ledger fact — drives the chip colours and, through the worst member, the
   * card's colour. */
  member_statuses: { member: string; status: MemberStatus }[];
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

/** One member chip on a card: its ledger fact, or `closed` — it left the
 * open list on a turn whose record the page has not re-read yet (shown
 * neutrally, never green). */
export type MemberState = MemberStatus | "closed";

/** A card's colour (ruling C-1): the WORST non-declined member — any `gap` →
 * red; else any `partial` → yellow; else green. All members declined → grey. */
export type CardTone = "red" | "yellow" | "green" | "grey";

/** The status pill: coverage wording, with the C-1b "likely match" for a
 * cluster nobody has asked yet. `null` = no pill (an open cluster). */
export type CoveragePill = "covered" | "partly" | "likely" | "declined";

export interface ClusterView {
  coverage: ClusterCoverage;
  members: { term: string; state: MemberState }[];
  tone: CardTone;
  pill: CoveragePill | null;
  /** Budget left AND coverage not covered/declined (`gap_coverage.is_askable`). */
  askable: boolean;
  /** Not askable ONLY because the budget is gone — a spent budget is shown,
   * not hidden (ADR-089 clause 3). */
  budgetSpent: boolean;
  /** Answered turns on this cluster across every door. */
  asked: number;
}

/** The worst-member rule (ruling C-1, founder's words: "3 green and one
 * yellow → card yellow; one green, one yellow, one red → card red; 5 green →
 * green"). Declined members never colour a card that has other members;
 * `closed` members (not re-read yet) do not colour it either. */
export function cardTone(states: MemberState[], coverage: ClusterCoverage): CardTone {
  const live = states.filter((st) => st !== "declined" && st !== "closed");
  if (live.includes("gap")) return "red";
  if (live.includes("partial")) return "yellow";
  if (live.length > 0) return "green";
  // Nothing live to judge by: all declined, or all closed by the last turn.
  if (states.length > 0 && states.every((st) => st === "declined")) return "grey";
  return coverage === "declined" ? "grey" : coverage === "open" ? "red" : coverage === "covered" ? "green" : "yellow";
}

/**
 * What a gap card renders — every field from the server: the persisted
 * cluster, overlaid by the turn that just answered it (`turn`, until the page
 * re-reads the analysis). No client-only state decides coverage.
 *
 * Member facts come from `member_statuses` (ruling C-1). A row that carries
 * none (legacy) falls back to the lists: covered/declined from `outcome`, an
 * open member reads `gap` in a Category C cluster and `partial` in a B one —
 * the colours the page showed before.
 */
export function clusterView(
  cluster: GapCluster,
  turn?: TurnClusterCoverage | null,
): ClusterView {
  const coverage = clusterCoverage(cluster, turn);
  const budget = clusterBudgetRemaining(cluster, turn);
  const facts = new Map<string, MemberStatus>();
  for (const entry of cluster.member_statuses ?? []) {
    if (entry && typeof entry.member === "string") facts.set(normMember(entry.member), entry.status);
  }
  const covered = new Set((cluster.outcome?.covered ?? []).map(normMember));
  const declined = new Set((cluster.outcome?.declined ?? []).map(normMember));
  const rowOpen = new Set((cluster.gaps ?? []).map(normMember));
  const turnOpen = turn ? new Set(turn.open_concepts.map(normMember)) : null;
  const openFallback: MemberStatus = cluster.category === "B" ? "partial" : "gap";

  const rowFact = (key: string): MemberStatus =>
    facts.get(key) ??
    (declined.has(key) ? "declined" : covered.has(key) ? "covered" : rowOpen.has(key) ? openFallback : "gap");

  const stateOf = (key: string): MemberState => {
    if (!turnOpen) return rowFact(key);
    if (turnOpen.has(key)) {
      // Still open after the turn: its row fact, unless the row called it
      // finished (then the turn is newer — it is open, i.e. not covered).
      const fact = rowFact(key);
      return fact === "gap" || fact === "partial" ? fact : openFallback;
    }
    // Left the open list on this turn: covered or declined, the page does not
    // know which until it re-reads the row — never guess green.
    const fact = rowFact(key);
    return fact === "covered" || fact === "declined" ? fact : "closed";
  };

  const members: { term: string; state: MemberState }[] = allClusterMembers(cluster).map((term) => ({
    term,
    state: stateOf(normMember(term)),
  }));
  if (turn) {
    const known = new Set(members.map((m) => normMember(m.term)));
    for (const term of turn.open_concepts) {
      if (!known.has(normMember(term))) members.push({ term, state: openFallback });
    }
  }

  const tone = cardTone(members.map((m) => m.state), coverage);
  const asked = cluster.outcome?.asked ?? 0;
  const pill: CoveragePill | null =
    coverage === "covered"
      ? "covered"
      : coverage === "declined"
        ? "declined"
        : coverage === "partly_covered"
          ? asked === 0 && tone === "yellow"
            ? "likely" // C-1b: nobody asked yet, no red member, at least one yellow
            : "partly"
          : null;

  const finished = coverage === "covered" || coverage === "declined";
  const hasBudget = budget === null ? true : budget > 0;
  return {
    coverage,
    members,
    tone,
    pill,
    askable: !finished && hasBudget,
    budgetSpent: !finished && !hasBudget,
    asked,
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
