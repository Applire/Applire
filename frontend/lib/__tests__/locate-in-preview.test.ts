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

import fs from "fs";
import path from "path";
import { describe, it, expect, beforeEach } from "vitest";
import {
  buildTextIndex,
  clearHighlights,
  countInText,
  findHits,
  foldVariants,
  highlightInDocument,
  normAudit,
  scanDocument,
  verbStem,
} from "../locate-in-preview";

// ADR-090 cl. 2 — the two normalisations are pinned to each other by ONE
// shared vector file, generated from the Python `_norm` and read by a Python
// test too (WP-A). Reading the repo file rather than copying the pairs in is
// the point: a copy would drift silently.
const VECTORS_PATH =
  process.env.ATS_NORM_VECTORS ?? path.resolve(__dirname, "../../../tests/files/ats_norm_vectors.json");
type Vector = { input: string; expected: string };

const vectorDoc: { vectors: Vector[] } | null = fs.existsSync(VECTORS_PATH)
  ? JSON.parse(fs.readFileSync(VECTORS_PATH, "utf-8"))
  : null;

describe("normAudit — parity with the backend ats_audit._norm (shared vector file)", () => {
  it("reads a non-empty shared vector file (a missing/empty file must not pass silently)", () => {
    expect(vectorDoc, `missing ${VECTORS_PATH}`).not.toBeNull();
    expect(vectorDoc!.vectors.length).toBeGreaterThanOrEqual(10);
  });

  for (const v of vectorDoc?.vectors ?? []) {
    it(`folds ${JSON.stringify(v.input)} to ${JSON.stringify(v.expected)}`, () => {
      expect(normAudit(v.input)).toBe(v.expected);
    });
    it(`maps ${JSON.stringify(v.input)} through the DOM index to the same string`, () => {
      const div = document.createElement("div");
      div.textContent = v.input;
      expect(buildTextIndex(div).text).toBe(v.expected);
    });
  }
});

describe("normAudit — the folds the audit applies", () => {
  it("drops soft hyphens, folds dashes to spaces, collapses whitespace, lowers, strips", () => {
    expect(normAudit("  Code-Review  ")).toBe("code review");
    expect(normAudit("Mentoring\u00adProgramme")).toBe("mentoringprogramme");
    expect(normAudit("CI\u2013CD")).toBe("ci cd");
    expect(normAudit("\uff21\uff29\u3000Governance")).toBe("ai governance");
  });

  it("uses Python's whitespace set, not JavaScript's", () => {
    // U+FEFF is \s in JS but not in Python; U+001C is the reverse.
    expect(normAudit("fe\ufeffff")).toBe("fe\ufeffff");
    expect(normAudit("\x1cgroup\x1f")).toBe("group");
  });
});

describe("findHits — whole-token, fold variants, stem fallback", () => {
  it("finds a form at a word boundary only", () => {
    const text = normAudit("AI governance board; not AI-governanceX or sub-AI governance");
    const hits = findHits(text, [{ form: "AI governance" }]);
    // "sub ai governance" (dash folded) is whole-token; "ai governancex" is not.
    expect(hits.map((h) => text.slice(h.start, h.end))).toEqual(["ai governance", "ai governance"]);
  });

  it("does not match inside a longer token", () => {
    expect(findHits(normAudit("Vertriebsreporting"), [{ form: "Vertrieb" }])).toHaveLength(0);
  });

  it("tries the singular/plural fold like _fold_variants", () => {
    expect(foldVariants("code reviews")).toEqual(["code reviews", "code review"]);
    expect(foldVariants("saas")).toEqual(["saas"]);
    expect(findHits(normAudit("Led code review standards"), [{ form: "Code reviews" }])).toHaveLength(1);
  });

  it("uses the verb-stem fallback only when the audit said stem", () => {
    const text = normAudit("Mentored four juniors");
    expect(findHits(text, [{ form: "Mentoring" }])).toHaveLength(0);
    expect(findHits(text, [{ form: "Mentoring", stem: true }])).toHaveLength(1);
    expect(verbStem("mentorship")).toBe("mentor");
    expect(verbStem("saas")).toBe("saas");
  });

  it("merges overlapping hits from several forms into one place each", () => {
    const text = normAudit("AI governance and data governance");
    const hits = findHits(text, [{ form: "AI governance" }, { form: "governance" }]);
    expect(hits.map((h) => text.slice(h.start, h.end))).toEqual(["ai governance", "governance"]);
  });

  it("returns nothing for an empty form", () => {
    expect(findHits("abc", [{ form: "  " }])).toEqual([]);
  });

  it("counts places in plain text", () => {
    expect(countInText("AI governance, AI Governance", [{ form: "ai governance" }])).toBe(2);
  });
});

describe("DOM index + highlight (the preview iframe's document)", () => {
  let doc: Document;
  beforeEach(() => {
    doc = document.implementation.createHTMLDocument("cv");
    doc.body.innerHTML =
      "<h2>Profile</h2><p>Focus on data quality and <b>AI</b> governance in regulated industries.</p>" +
      "<ul><li>Built the group-wide AI governance board</li><li>SQL</li><li>dbt</li></ul>";
  });

  it("keeps block boundaries as a space so list items never run together", () => {
    const { text } = buildTextIndex(doc.body);
    expect(text).toContain("sql dbt");
    expect(text).not.toContain("sqldbt");
  });

  it("finds a phrase that spans an inline element", () => {
    const r = scanDocument(doc, [{ form: "AI governance" }]);
    expect(r.total).toBe(2);
    expect(r.texts).toEqual(["AI governance", "AI governance"]);
  });

  it("marks every place, emphasises the current one, and clears back to the original text", () => {
    const before = doc.body.innerHTML;
    const r = highlightInDocument(doc, [{ form: "AI governance" }], 1);
    expect(r.total).toBe(2);
    const marks = Array.from(doc.querySelectorAll("mark[data-applire-hit]"));
    // Hit 0 spans <b>AI</b> + " governance" → two segments; hit 1 is one segment.
    expect(marks.map((m) => m.getAttribute("data-applire-hit"))).toEqual(["0", "0", "1"]);
    expect(doc.querySelectorAll("mark[data-applire-current]")).toHaveLength(1);
    expect(doc.querySelector("mark[data-applire-current]")!.textContent).toBe("AI governance");
    expect(doc.getElementById("applire-locate-style")).not.toBeNull();

    // A second show re-marks from scratch (no nested marks).
    highlightInDocument(doc, [{ form: "AI governance" }], 0);
    expect(doc.querySelectorAll("mark mark")).toHaveLength(0);

    clearHighlights(doc);
    expect(doc.querySelectorAll("mark")).toHaveLength(0);
    expect(doc.body.innerHTML).toBe(before);
  });

  it("reports 0 when the wording is not in the preview (the card says so)", () => {
    const r = highlightInDocument(doc, [{ form: "IT Data & AI Governance" }], 0);
    expect(r.total).toBe(0);
    expect(doc.querySelectorAll("mark")).toHaveLength(0);
  });
});
