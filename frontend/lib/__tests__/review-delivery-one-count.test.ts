// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * DH item 2 (Sorbet invite-gate delivery run, 2026-11-25): the header said "1 Punkt
 * braucht Dich" and the tab badge "1" while the card said "2 Punkte"; another card said
 * "3 Punkte" and listed six lines. One count from one source: the server's
 * `details_params.count` = the lines it marks `point`; the headline adds it once, and a
 * truth point the card lists is not counted again as its group-1 question.
 */
import { describe, expect, it } from "vitest";

import { buildDeliveryView, isOpenPointNote, openPointsHold, type DeliveryInputs } from "../review-groups";
import type { ATSReport, CheckNote } from "../ats-report";
import type { TruthfulnessReport } from "../truthfulness-display";

const truth: TruthfulnessReport = { version: "2", document_kind: "cv", counts: {}, stated_limit: "", claims: [] };

function held(notes: CheckNote[], count: number, presentUnsupported: string[] = []): NonNullable<ATSReport> {
  return {
    checks: [
      {
        id: "terminal-review", status: "fail", details_key: "terminal-review-open", details_params: { count },
        delivery_class: "A", resolver: "decide_open", open_points_hash: "h".repeat(16), notes,
      } as unknown as NonNullable<ATSReport>["checks"][number],
    ],
    keywords: {
      present: [], missing: [], missing_claimable: [], missing_honest_gap: [],
      present_unsupported: presentUnsupported, claimable_concepts: [],
    },
  } as unknown as NonNullable<ATSReport>;
}

const inputs = (atsReport: ATSReport): DeliveryInputs => ({
  atsReport, truthReport: truth, criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 }, gapClusters: [],
});

const finding = (i: number): CheckNote => ({ kind: "finding", text: `f${i}`, plain: `P${i}.`, point: true });
const fact = (key: string): CheckNote => ({ kind: "code", key, subjects: ["x"], text: "y", point: false });

describe("one count for the held card (DH item 2)", () => {
  it("three findings + three code facts: card 3, headline 3, download hint 3", () => {
    const report = held([finding(0), finding(1), finding(2), fact("cv.claim_missing"), fact("cv.skill_spelling"), fact("cv.skills_cap")], 3);
    const view = buildDeliveryView(inputs(report));
    expect(view.openPoints?.count).toBe(3);
    expect(view.count).toBe(3);
    expect(openPointsHold(report, null)).toEqual({ held: true, count: 3 });
    expect((view.openPoints?.check.notes ?? []).filter(isOpenPointNote)).toHaveLength(3);
  });

  it("a truth point the card lists and the group-1 question are one point, not two", () => {
    const term: CheckNote = {
      kind: "code", key: "oracle.posting_term", subjects: ["Maintenance"], text: "Maintenance",
      point: true, finding_key: "ats:maintenance",
    };
    const report = held([finding(0), finding(1), finding(2), term, fact("cv.claim_missing")], 4, ["Maintenance"]);
    const view = buildDeliveryView(inputs(report));
    expect(view.questions.map((q) => q.findingKey)).toContain("ats:maintenance");
    expect(view.openPoints?.count).toBe(4);
    expect(view.count).toBe(4);
  });

  it("a kept card adds nothing, and the statement it named is decided with it", () => {
    const term: CheckNote = { kind: "code", key: "oracle.posting_term", subjects: ["M"], text: "M", point: true, finding_key: "ats:m" };
    const report = held([term], 1, ["M"]);
    const kept = { decisions: [{ finding_key: "send:terminal-review", action: "kept", basis: "h".repeat(16) }] };
    const view = buildDeliveryView({ ...inputs(report), reviewState: kept as never });
    expect(view.openPoints?.acknowledged).toBe(true);
    expect(view.count).toBe(0);
  });

  it("an older row without marks: findings and truth lines are points, cv.* facts are not", () => {
    expect(isOpenPointNote({ kind: "finding", text: "a" })).toBe(true);
    expect(isOpenPointNote({ kind: "code", key: "oracle.unbacked", text: "a" })).toBe(true);
    expect(isOpenPointNote({ kind: "code", key: "audit.not_rechecked", text: "a" })).toBe(true);
    expect(isOpenPointNote({ kind: "code", key: "cv.skills_cap", text: "a" })).toBe(false);
    expect(isOpenPointNote({ kind: "code", key: "cv.skills_cap", text: "a", point: true })).toBe(true);
  });
});
