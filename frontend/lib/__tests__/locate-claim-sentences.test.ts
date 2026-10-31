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
 * D-4c(a) — the keyword card counts and locates only the occurrences that are
 * still a CLAIM. The fixture is the producer's own output
 * (`_keyword_coverage` on this letter, pinned by
 * `tests/unit/test_d4c_denial_compound_cover.py::test_the_claim_sentences_are_only_the_ones_the_flag_read`).
 */
import { describe, expect, it } from "vitest";
import { buildReviewGroups } from "../review-groups";
import { countInText, highlightInDocument, scanDocument } from "../locate-in-preview";
import type { ATSReport } from "../ats-report";

const LETTER = "Rheinwerk fertigt Verpackungen für Lebensmittelkunden – dieses Umfeld reizt mich. Bei Weberit habe ich die Lebensmittelproduktion geleitet.";
const PRODUCED = {
  present_unsupported: ["Lebensmittel"],
  present_unsupported_matches: { Lebensmittel: [{ form: "Lebensmittel", stem: false }] },
  present_unsupported_sentences: { Lebensmittel: ["bei weberit habe ich die lebensmittelproduktion geleitet."] },
  present_unsupported_employer_sentence: [],
};

function report(keywords: Record<string, unknown>): ATSReport {
  return {
    checks: [],
    keywords: { present: [], missing: [], missing_claimable: [], missing_honest_gap: [], claimable_concepts: [], ...keywords },
  } as unknown as ATSReport;
}

function targetsOf(keywords: Record<string, unknown>) {
  const groups = buildReviewGroups({ atsReport: report(keywords), truthReport: null, criticReport: null, gapClusters: null } as never);
  const item = groups.find((g) => g.id === 1)?.items.find((r) => r.label === "Lebensmittel");
  expect(item?.targets).toBeTruthy();
  return item!.targets!;
}

describe("D-4c(a) — only the claim occurrences are counted and located", () => {
  it("the producer's sentences narrow the term's targets: 1 place, not the employer clause", () => {
    const targets = targetsOf(PRODUCED);
    expect(targets[0].within).toEqual(PRODUCED.present_unsupported_sentences.Lebensmittel);
    expect(countInText(LETTER, targets)).toBe(1);
    expect(countInText(LETTER, targets.map(({ form, stem }) => ({ form, stem })))).toBe(2); // today's reach
  });

  it("„Zeig mir, wo“ marks the claim sentence's occurrence in the preview, never the employer's", () => {
    const doc = document.implementation.createHTMLDocument("letter");
    doc.body.innerHTML = `<div class="page"><p>Rheinwerk fertigt Verpackungen für Lebensmittelkunden – dieses Umfeld reizt mich.</p>
      <p>Bei Weberit habe ich die Lebensmittelproduktion geleitet.</p></div>`;
    const r = scanDocument(doc, targetsOf(PRODUCED));
    expect(r.total).toBe(1);
    expect(r.texts).toEqual(["Lebensmittel"]);
    highlightInDocument(doc, targetsOf(PRODUCED), 0);
    const marks = Array.from(doc.querySelectorAll("mark"));
    expect(marks).toHaveLength(1);
    expect(marks[0].closest("p")!.textContent).toContain("Bei Weberit");
  });

  it("a legacy report ({}) keeps today's behaviour", () => {
    const targets = targetsOf({ ...PRODUCED, present_unsupported_sentences: {} });
    expect(targets[0].within).toBeUndefined();
    expect(countInText(LETTER, targets)).toBe(2);
  });

  it("sentences that align nowhere in the preview (PDF text order) fall back to every occurrence, never to 0", () => {
    const targets = targetsOf({ ...PRODUCED, present_unsupported_sentences: { Lebensmittel: ["ein satz der so nicht im dokument steht."] } });
    expect(countInText(LETTER, targets)).toBe(2);
  });
});
