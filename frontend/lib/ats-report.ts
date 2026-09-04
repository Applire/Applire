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
 * The ATS report's SHAPE and the two pure helpers that read it.
 *
 * Extracted from `components/cv/ATSChecksPanel.tsx` by E058/US300 so the
 * document review surface (`lib/review-groups.ts`, a pure module) can read the
 * same report without importing a React component — and, more importantly, so
 * the check-id and detail-key rules exist ONCE (ADR-066). `ATSChecksPanel`
 * imports them from here and re-exports the types it used to declare, so every
 * existing `import type { ATSReport } from "@/components/cv/ATSChecksPanel"`
 * keeps working.
 *
 * Nothing here computes anything the backend computes. ADR-081 clause 2: the
 * surface reads the producer's populations, it never recomputes them.
 */

export type ATSCheck = {
  id: string;
  // E057/ADR-079 clause 4: a THIRD status for a check that genuinely cannot
  // be evaluated on this artefact (e.g. the page-length band on a .docx
  // export). Counted in its own bucket by the backend's `_finish()` — never
  // a pass, never a fail.
  status: "pass" | "fail" | "not_applicable";
  details?: string | null;
  // E042 follow-up (ADR-038): machine-readable twin of `details` for bands the
  // frontend localises; `details` stays the EN fallback for legacy reports.
  details_key?: string | null;
  details_params?: Record<string, string | number> | null;
  // E056/ADR-077 clause 5: structured driver for a fail band — currently only
  // {"pinned_facts": N} on the page-length check (N present pinned facts).
  driver?: Record<string, number> | null;
};

// E056/ADR-077 clauses 3+5: one fact pin's measured fate on THIS document —
// present in the tailored twin, stale (excluded from generation), or removed
// by a truth floor (hierarchy: truth > pin, never silent). Ship-and-report,
// never a gate.
export type PinnedFactReportEntry = {
  pin_id: string;
  entry_type: string;
  quote: string;
  present: boolean;
  stale: boolean;
  removed_by_truth_floor?: boolean;
  // #580: the job's do-not-claim terms this pinned quote carries — a fact
  // about the quote, never a statement about why the pin is absent. Optional
  // for back-compat with reports persisted before this field existed.
  ledger_conflict?: string[];
};

export type ATSReport = {
  checks: ATSCheck[];
  // null/absent = audited without pin context (legacy reports, no pins).
  pinned_facts?: PinnedFactReportEntry[] | null;
  keywords: {
    present: string[];
    missing: string[];
    // US203 (ADR-048): a missing keyword the candidate HAS per the Keyword Ledger
    // (a surfacing miss — fixable) vs one they genuinely lack (an honest gap, never
    // something to fabricate). Optional for back-compat with legacy reports.
    missing_claimable?: string[];
    missing_honest_gap?: string[];
    // #117 (ADR-048 fourth quadrant): present in the document WITHOUT profile backing —
    // an unsupported claim (truthfulness warning). Optional for back-compat.
    present_unsupported?: string[];
    // E048/US266 (#249 option b): EVERY claimable Keyword Ledger entry's surface
    // forms (concept name included), regardless of presence in the document.
    claimable_concepts?: string[];
  };
} | null;

/** Strip a trailing numeric index: `work-1` → `work`, `body-3` → `body`. */
export const baseId = (id: string): string => id.replace(/-\d+$/, "");

// E042 follow-up (ADR-038): detail keys with a translation under
// `ats.checkDetails`. Only whitelisted keys go through `t()` — an unknown key
// from a newer backend falls back to the EN `details` string instead of
// rendering a raw key path.
export const LOCALIZED_DETAIL_KEYS = new Set([
  "page-length-target",
  // #238 (founder-acceptance F4): an explicit page target the condense loop
  // could not hit — a genuine miss, never dressed up as senior-profile advice.
  "page-length-target-missed",
  "page-length-senior",
  "page-length-exhausted",
  "page-length-exceeds",
  "page-length-letter",
  // ADR-079 cl. 4 (E057): the .docx export has no intrinsic pagination, so the
  // band is neither pass nor fail.
  "page-length-not-applicable",
  // #391 interim (ADR-076 amendment 4 point 6): measurement-only advisory.
  "skills-weak-vault-tie",
]);

/**
 * Should this check's `details` be rendered through `t()`?
 *
 * The params check matters and is not decoration: next-intl does NOT throw on a
 * missing ICU variable — it renders the raw key path — so a keyed check without
 * params (a partially-migrated persisted report) must take the EN fallback.
 */
export function usesLocalizedDetail(check: ATSCheck): boolean {
  return Boolean(
    check.details_key &&
      check.details_params &&
      LOCALIZED_DETAIL_KEYS.has(check.details_key),
  );
}
