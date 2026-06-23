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

import type { ReviewChange } from "@/components/review/WhatChangedReview";
import { getApiErrorMessage } from "./errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/** Wire shape of a FieldChange from the backend (snake_case). */
export interface ApiFieldChange {
  section: string;
  field: string;
  action: "added" | "updated" | "merged";
  old_value?: unknown;
  new_value?: unknown;
  rationale?: string | null;
  rationale_key?: string | null;
}

/** Map a backend FieldChange to the component's camelCase ReviewChange. */
export function toReviewChange(fc: ApiFieldChange): ReviewChange {
  return {
    section: fc.section,
    field: fc.field,
    action: fc.action,
    oldValue: fc.old_value,
    newValue: fc.new_value,
    rationale: fc.rationale ?? null,
    rationaleKey: fc.rationale_key ?? null,
  };
}

export interface CvProfileDiff {
  items: ReviewChange[];
  grounded: boolean;
}

/** US147 — deterministic diff of a generated CV vs the Master Profile (no LLM). */
export async function getCvProfileDiff(cvId: string): Promise<CvProfileDiff> {
  const res = await fetch(`${API_BASE}/api/cv/${cvId}/profile-diff`);
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res));
  }
  const data = await res.json();
  return {
    items: (data.items ?? []).map(toReviewChange),
    grounded: Boolean(data.grounded),
  };
}

export interface ProfileChanges {
  enrichmentHistory: { source: string; timestamp: string; changes: ReviewChange[] }[];
  pendingConflicts: ReviewChange[];
}

/** US148 — the decision trail + pending conflicts, for the merge/interview surfaces. */
export async function getProfileChanges(): Promise<ProfileChanges> {
  const res = await fetch(`${API_BASE}/api/profile/changes`);
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res));
  }
  const data = await res.json();
  return {
    enrichmentHistory: (data.enrichment_history ?? []).map((rec: { source: string; timestamp: string; changes: ApiFieldChange[] }) => ({
      source: rec.source,
      timestamp: rec.timestamp,
      changes: (rec.changes ?? []).map(toReviewChange),
    })),
    pendingConflicts: (data.pending_conflicts ?? []).map((c: ApiFieldChange & { existing_value?: unknown; incoming_value?: unknown }) =>
      toReviewChange({
        section: c.section,
        field: c.field,
        action: "updated",
        old_value: (c as { existing_value?: unknown }).existing_value,
        new_value: (c as { incoming_value?: unknown }).incoming_value,
        rationale: c.rationale ?? null,
      }),
    ),
  };
}

/**
 * True when an actual merge happened (a second import combined into existing data):
 * a non-interview change with action "merged"/"updated", or a pending conflict.
 * A first single-CV import yields only "added" changes → false (#67).
 */
export function hasMergeReview(trail: ProfileChanges): boolean {
  const records = trail.enrichmentHistory ?? [];
  const mergeChanges = records
    .filter((r) => r.source !== "interview")
    .flatMap((r) => r.changes ?? [])
    .filter((c) => c.action === "merged" || c.action === "updated");
  const conflicts = trail.pendingConflicts ?? [];
  return mergeChanges.length > 0 || conflicts.length > 0;
}
