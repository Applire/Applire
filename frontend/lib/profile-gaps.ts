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
 * Per-entry work-experience gap counting for the profile page's Enrich
 * affordance (#155).
 *
 * NOTE: this is a deliberately lean frontend heuristic, NOT the full backend
 * expected-fields model (services/profile/completeness.py). The end_date rule
 * mirrors the backend presence predicate exactly: an empty end_date is a gap
 * UNLESS the entry is explicitly marked as the current position
 * (is_current === true — tri-state; null/undefined means unknown).
 * Follow-up: align the remaining fields with the backend model if the
 * per-entry count should ever equal the interview's question count.
 */
export interface WorkEntryGapFields {
  description?: string | null;
  achievements?: string[] | null;
  end_date?: string | null;
  is_current?: boolean | null;
}

/**
 * #382 (PO decision 2026-08-08, Option A) — the join key between a backend
 * health issue and the work entry on screen.
 *
 * Mirrors the backend's `_entry_label` exactly, including Python's
 * `f"{role} @ {company}".strip(" @")` behaviour for a one-sided entry. It is
 * used INSTEAD of `WorkEntry.id` because `id` has a UUID default factory: an
 * entry persisted before that field existed is re-keyed on every load, so two
 * responses in the same page load can disagree about it.
 */
export function workEntryLabel(entry: {
  role?: string | null;
  title?: string | null;
  company?: string | null;
}): string {
  const role = (entry.role ?? entry.title ?? "").trim();
  const company = (entry.company ?? "").trim();
  return `${role} @ ${company}`.replace(/^[\s@]+|[\s@]+$/g, "");
}

/** Minimal structural shape of the health issues this module reads. */
export interface BudgetUnitIssue {
  thread: string;
  source_record_ref?: string | null;
}

/**
 * The entry labels whose budget figure states no unit (#382).
 *
 * The RULE lives in the backend (ADR-066: `utils/budget_unit.py`, surfaced as
 * the `unit` health thread); this reads the answer only, so the page can put
 * the fix affordance next to the affected field rather than only in the hub —
 * the PO's standing condition on omitting the figure from delivered documents.
 */
export function budgetUnitIssueLabels(
  issues: BudgetUnitIssue[] | undefined | null,
): Set<string> {
  const labels = new Set<string>();
  for (const issue of issues ?? []) {
    if (issue.thread !== "unit") continue;
    const label = (issue.source_record_ref ?? "").trim();
    if (label) labels.add(label);
  }
  return labels;
}

export function countWorkEntryGaps(entry: WorkEntryGapFields): number {
  let count = 0;
  // Content gap — mirrors the backend floor field "achievements". `description`
  // is accepted as a legacy fallback (old mocks/records); note the backend
  // WorkEntry schema has NO description field, so checking description alone
  // made this gap permanent on every real entry (#155 secondary bug: the
  // Enrich button never disappeared).
  if (!entry.description && !entry.achievements?.length) count++;
  // #155 — mirrors backend field_present(entry, "end_date")
  if (!entry.end_date && entry.is_current !== true) count++;
  return count;
}
