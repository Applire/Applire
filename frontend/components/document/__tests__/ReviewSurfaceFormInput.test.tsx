// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Ruling B3-10 / RULING FO-1 = A (2026-10-23, WP-FO): a letter sentence the Oracle
 * marks `letter_form_input` is the candidate's own statement for that letter. It
 * sits in a small neutral section "Aus Deiner Eingabe im Formular" below the
 * group-1 list — outline chip, NO buttons, not in the headline, not in the
 * "k of n decided" bar — and its rationale is said in the UI language.
 */
import React from "react";
import { render, screen, within, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { TruthfulnessReport, TruthfulnessClaimResult } from "@/lib/truthfulness-display";
import { buildFormInputRows } from "@/lib/review-groups";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const SENTENCE = "Die dauerhafte Führungsspanne wäre für mich der nächste Schritt nach meiner Standortvertretung mit etwa 90 Mitarbeitenden.";

function claim(text: string, verdict: TruthfulnessClaimResult["verdict"], i = 0): TruthfulnessClaimResult {
  return { claim: { text, location: `body.paragraphs[1][${i}]`, kind: "sentence" }, verdict };
}

const FORM_CLAIM = claim(SENTENCE, {
  verdict: "not_applicable",
  checker: "letter_form_input",
  evidence: [{ kind: "letter_form", ref: "pre_gen_inputs.motivation", excerpt: "…" }],
  detail: "Built from what you typed into the letter form for this letter (motivation) — …",
  detail_key: "fromLetterForm",
  detail_params: { field: "motivation" },
});
const RED_CLAIM = claim(
  "Ich habe 25 Jahre Erfahrung.",
  { verdict: "unbacked", checker: "numbers", evidence: [], detail: "No vault evidence for figure(s): 25.",
    detail_key: "figuresNoEvidence", detail_params: { figures: "25" } },
  1,
);

function truth(claims: TruthfulnessClaimResult[]): TruthfulnessReport {
  return { version: "1.4", document_kind: "cover_letter", claims, counts: {}, stated_limit: "" };
}

const BASE: ReviewSurfaceProps = {
  documentKind: "cover-letter",
  documentId: "cl-1",
  atsReport: { checks: [{ id: "contact-0", status: "pass" }], keywords: {
    present: [], missing: [], missing_claimable: [], missing_honest_gap: [], present_unsupported: [], claimable_concepts: [],
  } },
  truthReport: truth([FORM_CLAIM]),
  criticReport: { ran: true, mount: "letter", advisories: [], dropped_citations: 0 },
  gapClusters: [],
};

afterEach(cleanup);

describe("buildFormInputRows", () => {
  it("lists only letter_form_input sentences, once each", () => {
    const rows = buildFormInputRows(truth([FORM_CLAIM, { ...FORM_CLAIM, claim: { ...FORM_CLAIM.claim, location: "x" } }, RED_CLAIM]));
    expect(rows.map((r) => r.text)).toEqual([SENTENCE]);
    expect(rows[0].detailKey).toBe("fromLetterForm");
  });
  it("is empty for an older report", () => {
    expect(buildFormInputRows(null)).toEqual([]);
  });
});

describe("ReviewSurface — Aus Deiner Eingabe im Formular", () => {
  it("shows the sentence in a neutral section with no buttons, in the UI language", () => {
    render(withIntl(<ReviewSurface {...BASE} />, "de"));
    const section = screen.getByTestId("review-form-input");
    expect(within(section).getByText("Aus Deiner Eingabe im Formular")).toBeTruthy();
    expect(within(section).getByText(SENTENCE)).toBeTruthy();
    expect(within(section).getByText("Formular")).toBeTruthy();
    expect(within(section).getByText(/Warum diese Stelle/)).toBeTruthy();
    expect(within(section).queryByText(/Built from what you typed/)).toBeNull(); // not the English detail
    expect(within(section).queryAllByRole("button")).toHaveLength(0);
    // not a group-1 finding: no row, no card for it
    expect(document.querySelectorAll('[data-testid^="review-item-g1-"]')).toHaveLength(0);
  });

  it("is not counted in the group-1 progress", () => {
    render(withIntl(<ReviewSurface {...BASE} truthReport={truth([FORM_CLAIM, RED_CLAIM])} />, "en"));
    expect(document.querySelectorAll('[data-testid^="review-item-g1-"]')).toHaveLength(1);
    expect(screen.getByTestId("review-form-input-row").textContent).toContain(SENTENCE);
    expect(screen.queryByText(/of 2 decided/)).toBeNull();
  });

  it("renders nothing when no sentence came from the form", () => {
    render(withIntl(<ReviewSurface {...BASE} truthReport={truth([RED_CLAIM])} />, "en"));
    expect(screen.queryByTestId("review-form-input")).toBeNull();
  });

  it("says the restatement judge's rationale in the UI language", () => {
    const borrowed = claim("Mit 90 Mitarbeitenden …", {
      verdict: "unbacked", checker: "restatement_judgement", evidence: [],
      detail: "The only vault evidence for this claim's figure belongs to a different position …",
      detail_key: "restatementBorrowed",
    });
    render(withIntl(<ReviewSurface {...BASE} truthReport={truth([borrowed])} />, "de"));
    expect(screen.queryByText(/The only vault evidence/)).toBeNull();
    expect(screen.getAllByText(/nur eine andere Station/).length).toBeGreaterThan(0);
  });
});
