// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #803 belt (founder ruling T-2 = B, 2026-10-24, Hazelnut build 4): a letter term
// whose every occurrence sits in a sentence naming the employer
// (`keywords.present_unsupported_employer_sentence`, ux-audit-2 marcus-071
// "Konsumgüter") stays listed with take-out + edit, but neutral: no
// add-to-profile handle and not counted in the headline or the badge.

import React from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { buildReviewGroups, openGroup1Count } from "@/lib/review-groups";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

function ats(employerSentence: string[] | undefined): ATSReport {
  return {
    checks: [],
    keywords: {
      present: ["Konsumgüter", "Investitionsplanung"],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: ["Konsumgüter", "Investitionsplanung"],
      present_unsupported_matches: {
        Konsumgüter: [{ form: "Konsumgüter", stem: false }],
        Investitionsplanung: [{ form: "Investitionsplanung", stem: false }],
      },
      present_unsupported_employer_sentence: employerSentence,
      claimable_concepts: [],
    },
  };
}

const CRITIC: OutcomeCriticReport = { ran: true, mount: "letter", advisories: [], dropped_citations: 0 };

function props(report: ATSReport): ReviewSurfaceProps {
  return {
    documentKind: "cover-letter",
    documentId: "letter-1",
    atsReport: report,
    truthReport: { version: "1", document_kind: "cover_letter", claims: [], counts: {}, stated_limit: "" },
    criticReport: CRITIC,
    gapClusters: null,
    hasClusterProducer: false,
  };
}

const verdictNumber = () => Number(screen.getByTestId("review-verdict").textContent?.match(/\d+/)?.[0]);

describe("#803 belt — a term only in sentences about the employer", () => {
  it("is listed but not counted: headline and badge count the other term only", () => {
    render(withIntl(<ReviewSurface {...props(ats(["Konsumgüter"]))} />, "de"));
    expect(verdictNumber()).toBe(1);
    const g1 = buildReviewGroups({ atsReport: ats(["Konsumgüter"]), truthReport: null, criticReport: CRITIC, gapClusters: null })
      .find((g) => g.id === 1)!;
    expect(g1.items.map((i) => i.label)).toEqual(["Konsumgüter", "Investitionsplanung"]);
    expect(openGroup1Count(g1.items, null)).toBe(1);
  });

  it("its card says why, offers no add handle, keeps take-out — no editor handle (de, ruling C-1 (2))", () => {
    render(withIntl(<ReviewSurface {...props(ats(["Konsumgüter"]))} />, "de"));
    const card = screen.getByTestId("review-card");
    expect(card.getAttribute("data-finding-key")).toBe("ats:konsumgüter");
    expect(within(card).getByTestId("review-card-why").textContent).toContain(
      "„Konsumgüter“ steht nur in Sätzen über das Unternehmen, bei dem Du Dich bewirbst",
    );
    expect(within(card).queryByTestId("review-action-add")).toBeNull();
    expect(within(card).getByTestId("review-action-takeout")).toBeTruthy();
    expect(within(card).queryByTestId("review-action-edit")).toBeNull();
  });

  it("a report without the field keeps both terms red and counted", () => {
    render(withIntl(<ReviewSurface {...props(ats(undefined))} />, "en"));
    expect(verdictNumber()).toBe(2);
    expect(within(screen.getByTestId("review-card")).getByTestId("review-action-add")).toBeTruthy();
  });
});
