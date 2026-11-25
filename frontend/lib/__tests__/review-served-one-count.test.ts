// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * MD-G32 fix round 2 (X-2): the one count, read from a CAPTURED served response — the
 * fixtures are written by the backend test `tests/unit/test_dh_fix2_served_one_count.py`
 * from `get_cv_ats_report` / `get_cover_letter_ats_report`, never by hand. Before the fix
 * the wire dropped `finding_key`, so every truth point the card lists was counted again
 * as its group-1 question (headline and tab badge 17 against a card of 8).
 */
import { describe, expect, it } from "vitest";

import { buildDeliveryView, isAdviceNote, isOpenPointNote, openPointsHold } from "../review-groups";
import type { ATSReport } from "../ats-report";
import type { TruthfulnessReport } from "../truthfulness-display";
import servedCv from "./fixtures/served-held-cv.json";
import servedLetter from "./fixtures/served-held-letter.json";

const cases = [
  ["CV", servedCv],
  ["letter", servedLetter],
] as const;

describe.each(cases)("served held %s: one count on every surface", (_name, served) => {
  const atsReport = served.ats.report as unknown as ATSReport;
  const truthReport = served.truth as unknown as TruthfulnessReport;
  const view = buildDeliveryView({
    atsReport, truthReport, criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 }, gapClusters: [],
  });

  it("the card counts its point lines — the number the server named", () => {
    const card = view.openPoints!;
    const notes = card.check.notes ?? [];
    expect(card.count).toBe(5);
    expect(notes.filter(isOpenPointNote)).toHaveLength(5);
    expect(notes.filter(isAdviceNote).length).toBeGreaterThan(0);
    expect(openPointsHold(atsReport, null)).toEqual({ held: true, count: 5 });
  });

  it("every truth line of the card keeps its group-1 key over the wire", () => {
    const keyed = (view.openPoints!.check.notes ?? []).filter((n) => n.finding_key);
    expect(keyed).toHaveLength(3);
    const questionKeys = view.questions.map((q) => q.findingKey);
    for (const n of keyed) expect(questionKeys).toContain(n.finding_key);
  });

  it("headline and tab badge = the card + the translate card, each truth point once", () => {
    expect(view.translate).toHaveLength(1);
    expect(view.count).toBe(5 + 1);
  });
});
