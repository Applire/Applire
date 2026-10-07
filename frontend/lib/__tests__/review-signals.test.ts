// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #702 / #703 — the review panel's data layer for the cross-document section and
// the repeated-demand signal (RULING R-2 = A, R-3 = A). Pure; no React.

import { describe, it, expect } from "vitest";
import {
  buildCrossDocument,
  buildGroup1Rows,
  buildReviewGroups,
  repeatedDemandSignals,
  type ReviewInputs,
} from "../review-groups";
import type { ATSReport, CheckSignal } from "../ats-report";
import type { ReviewState } from "../api/document-review";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

const TRANSFER =
  "Hygiene- und Dokumentationsdisziplin aus Kosmetik-Verpackungen, einem Sauberraumbereich seit 2021 und zehn Jahren ISO-9001-Audit-Praxis sind übertragbare Grundlagen.";
const KEY = "critic:hygiene- und dokumentationsdisziplin";

function signal(term: string, weight: "open" | "landed", rounds = 2): CheckSignal {
  return { kind: "repeated_demand", term, rounds, total_rounds: 8, in_document: weight === "landed", weight };
}

function ats(missing_claimable: string[] = [], signals: CheckSignal[] = []): ATSReport {
  return {
    checks: [{ id: "terminal-review", status: "fail", details: "x", signals }],
    keywords: {
      present: [],
      missing: [],
      missing_claimable,
      missing_honest_gap: [],
      present_unsupported: [],
      claimable_concepts: [],
    },
  };
}

const CRITIC: OutcomeCriticReport = {
  ran: true,
  mount: "letter",
  dropped_citations: 0,
  advisories: [
    { concept: "Kosmetik-Verpackungen", kind: "letter_only", letter_state: TRANSFER, changed: false, message: { de: "d", en: "e" } },
    { concept: "Sauberraumbereich seit 2021", kind: "letter_only", letter_state: TRANSFER, changed: false, message: { de: "d", en: "e" } },
    { concept: "Teamgröße", kind: "numeric_inconsistency", cv_state: "38", letter_state: "40", changed: false, message: { de: "d", en: "e" } },
  ],
  cross_document: [
    {
      key: KEY,
      letter_state: TRANSFER,
      concepts: ["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021"],
      kinds: ["letter_only"],
      weight: "high",
      figures: ["2021", "zehn Jahren"],
    },
  ],
};

function inputs(over: Partial<ReviewInputs> = {}): ReviewInputs {
  return { atsReport: ats(), truthReport: { version: "1", document_kind: "cover_letter", claims: [], counts: {}, stated_limit: "" }, criticReport: CRITIC, gapClusters: [], hasClusterProducer: false, ...over };
}

const group = (inp: ReviewInputs, id: 1 | 2 | 3 | 4) => buildReviewGroups(inp).find((g) => g.id === id)!;

describe("#702 cross-document section", () => {
  it("lists the derived items with no decision as open", () => {
    const s = buildCrossDocument(CRITIC, null);
    expect(s.unknown).toBe(false);
    expect(s.rows).toHaveLength(1);
    expect(s.rows[0].status).toBe("open");
    expect(s.rows[0].item.weight).toBe("high");
  });

  it("labels an item decided by a critic: decision (the critic does not re-run)", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: KEY, label: TRANSFER, action: "kept", at: "2026-10-07T12:00:00Z", undo: null }],
    };
    const s = buildCrossDocument(CRITIC, state);
    expect(s.rows[0].status).toBe("kept");
    expect(s.decided).toBe(1);
  });

  it("is unknown — never empty — when the critic did not run (ADR-081 cl. 9)", () => {
    expect(buildCrossDocument({ ...CRITIC!, ran: false }, null).unknown).toBe(true);
    expect(buildCrossDocument(null, null).unknown).toBe(true);
  });

  it("moves letter_only/letter_richer advisories out of group 4 and keeps the others", () => {
    const g4 = group(inputs(), 4);
    const labels = g4.items.filter((i) => i.kind === "advisory").map((i) => i.label);
    expect(labels).toEqual(["Teamgröße"]);
  });

  it("keeps every advisory in group 4 when the backend did not derive cross_document", () => {
    const legacy = { ...CRITIC!, cross_document: undefined };
    const labels = group(inputs({ criticReport: legacy }), 4)
      .items.filter((i) => i.kind === "advisory")
      .map((i) => i.label);
    expect(labels).toEqual(["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "Teamgröße"]);
  });

  it("a critic: decision never becomes a group-1 row", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: KEY, label: TRANSFER, action: "taken_out", at: "2026-10-07T12:00:00Z", undo: null }],
    };
    expect(buildGroup1Rows([], state)).toEqual([]);
  });
});

describe("#703 repeated-demand signal (RULING R-3 = A)", () => {
  it("annotates the open term's group-2 row and lists it first", () => {
    const g2 = group(
      inputs({ atsReport: ats(["OEE", "Maschinendatenerfassung"], [signal("Maschinendatenerfassung", "open")]) }),
      2,
    );
    expect(g2.items[0].label).toBe("Maschinendatenerfassung");
    expect(g2.items[0].signal?.rounds).toBe(2);
    expect(g2.items[1].signal).toBeUndefined();
  });

  it("an open term the ATS list does not name becomes its own group-2 row", () => {
    const g2 = group(inputs({ atsReport: ats(["OEE"], [signal("Industrie 4.0", "open", 3)]) }), 2);
    expect(g2.items.map((i) => i.label)).toEqual(["Industrie 4.0", "OEE"]);
  });

  it("a landed term is a group-4 signal row, not a group-2 row", () => {
    const inp = inputs({ atsReport: ats([], [signal("Arbeitssicherheit", "landed", 3)]) });
    expect(group(inp, 2).items).toHaveLength(0);
    const row = group(inp, 4).items.find((i) => i.kind === "signal")!;
    expect(row.label).toBe("Arbeitssicherheit");
    expect(row.signal?.rounds).toBe(3);
  });

  it("reads only the terminal-review check, never recomputes", () => {
    const report = ats([], [signal("A", "open")]);
    report!.checks.push({ id: "page-length", status: "pass", signals: [signal("B", "open")] });
    expect(repeatedDemandSignals(report).map((s) => s.term)).toEqual(["A"]);
    expect(repeatedDemandSignals(null)).toEqual([]);
  });

  it("a pre-#703 report yields no signal rows", () => {
    const report = ats(["OEE"]);
    delete report!.checks[0].signals;
    expect(group(inputs({ atsReport: report }), 2).items.every((i) => !i.signal)).toBe(true);
    expect(group(inputs({ atsReport: report }), 4).items.some((i) => i.kind === "signal")).toBe(false);
  });
});
