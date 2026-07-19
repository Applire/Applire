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

import { describe, it, expect, vi, afterEach } from "vitest";
import { toReviewChange, getCvProfileDiff, getProfileChanges, hasMergeReview } from "../api/review";
import type { ProfileChanges } from "../api/review";

afterEach(() => vi.restoreAllMocks());

function fetchMock(body: object, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: () => Promise.resolve(body), statusText: "OK" });
}

describe("toReviewChange", () => {
  it("maps snake_case wire shape to camelCase (incl. rationale_key)", () => {
    const r = toReviewChange({ section: "skills", field: "skills", action: "added", old_value: null, new_value: "Rust", rationale: "why", rationale_key: "new_skill" });
    expect(r).toEqual({ section: "skills", field: "skills", action: "added", oldValue: null, newValue: "Rust", rationale: "why", rationaleKey: "new_skill" });
  });
});

describe("getCvProfileDiff", () => {
  it("maps items and grounded", async () => {
    global.fetch = fetchMock({ items: [{ section: "skills", field: "skills", action: "added", new_value: "Rust", rationale: "x" }], grounded: false });
    const result = await getCvProfileDiff("cv-1");
    expect(result.grounded).toBe(false);
    expect(result.items[0]).toMatchObject({ section: "skills", newValue: "Rust" });
  });

  it("throws on non-ok", async () => {
    global.fetch = fetchMock({ detail: "nope" }, false, 404);
    await expect(getCvProfileDiff("cv-1")).rejects.toThrow();
  });
});

describe("getProfileChanges", () => {
  it("maps enrichment history and pending conflicts", async () => {
    global.fetch = fetchMock({
      enrichment_history: [
        { source: "cv_upload", timestamp: "2026-06-13T00:00:00Z", changes: [{ section: "work_experience", field: "work_experience", action: "merged", new_value: "X", rationale: "r" }] },
      ],
      pending_conflicts: [
        { section: "work_experience", field: "start_date", existing_value: "2020-01", incoming_value: "2019-01", rationale: null },
      ],
    });
    const result = await getProfileChanges();
    expect(result.enrichmentHistory).toHaveLength(1);
    expect(result.enrichmentHistory[0].changes[0].newValue).toBe("X");
    expect(result.pendingConflicts[0]).toMatchObject({ action: "updated", oldValue: "2020-01", newValue: "2019-01" });
  });
});

describe("hasMergeReview", () => {
  function change(action: "added" | "updated" | "merged") {
    return { section: "skills", field: "skills", action, oldValue: null, newValue: "X", rationale: null, rationaleKey: null };
  }
  function trail(partial: Partial<ProfileChanges>): ProfileChanges {
    return { enrichmentHistory: [], pendingConflicts: [], ...partial };
  }

  it("returns false for an empty trail", () => {
    expect(hasMergeReview(trail({}))).toBe(false);
  });

  it("returns false for a first single import (only 'added' changes)", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "cv_upload", timestamp: "t", changes: [change("added"), change("added")] }] })),
    ).toBe(false);
  });

  it("returns true for a non-interview 'merged' change", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "cv_upload", timestamp: "t", changes: [change("merged")] }] })),
    ).toBe(true);
  });

  it("returns true for a non-interview 'updated' change", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "cv_upload", timestamp: "t", changes: [change("updated")] }] })),
    ).toBe(true);
  });

  it("returns true when there are pending conflicts even with no merge changes", () => {
    expect(hasMergeReview(trail({ pendingConflicts: [change("updated")] }))).toBe(true);
  });

  it("excludes interview-source changes (returns false even with a 'merged' change)", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "interview", timestamp: "t", changes: [change("merged")] }] })),
    ).toBe(false);
  });

  it("excludes agent_interview-source changes — agent claims are testimony, not import merges (US256)", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "agent_interview", timestamp: "t", changes: [change("merged")] }] })),
    ).toBe(false);
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "agent_interview", timestamp: "t", changes: [change("updated")] }] })),
    ).toBe(false);
  });

  it("returns false for a manual_edit 'updated' change with no import-source merge (#67)", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "manual_edit", timestamp: "t", changes: [change("updated")] }] })),
    ).toBe(false);
  });

  // Flow-scoping (Spaghettieis UAT): the gaps page must only announce a merge
  // that happened in THIS run — a merge from the initial onboarding import
  // otherwise haunts every later follow-up flow's gap view.
  it("ignores import merges from BEFORE `since` (older run)", () => {
    expect(
      hasMergeReview(
        trail({ enrichmentHistory: [{ source: "cv_upload", timestamp: "2026-07-13T09:00:00Z", changes: [change("merged")] }] }),
        { since: "2026-07-13T10:00:00Z" },
      ),
    ).toBe(false);
  });

  it("counts import merges AT or AFTER `since` (this run)", () => {
    expect(
      hasMergeReview(
        trail({ enrichmentHistory: [{ source: "cv_upload", timestamp: "2026-07-13T10:00:05Z", changes: [change("merged")] }] }),
        { since: "2026-07-13T10:00:00Z" },
      ),
    ).toBe(true);
  });

  it("keeps pending conflicts actionable regardless of `since`", () => {
    expect(
      hasMergeReview(trail({ pendingConflicts: [change("updated")] }), { since: "2026-07-13T10:00:00Z" }),
    ).toBe(true);
  });

  it("returns true for a cv_upload 'merged' change (import source still counts)", () => {
    expect(
      hasMergeReview(trail({ enrichmentHistory: [{ source: "cv_upload", timestamp: "t", changes: [change("merged")] }] })),
    ).toBe(true);
  });
});
