// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// 786:47 (founder 2026-10-22, Hazelnut build 4): the letter audit's
// `employer-repetition` advisory reaches the review surface in the UI language,
// as an info row in group 4 — never counted in the headline.

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const REPORT: ATSReport = {
  checks: [
    {
      id: "employer-repetition",
      status: "pass",
      details: "'Schwarzwald Präzision GmbH' opens 4 sentences in paragraph 2 — a name repeated …",
      details_key: "employer-repetition",
      details_params: { employer: "Schwarzwald Präzision GmbH", paragraph: 2, count: 4, more: 1 },
    },
  ],
  keywords: {
    present: [], missing: [], missing_claimable: [], missing_honest_gap: [],
    present_unsupported: [], claimable_concepts: [],
  },
};
const CRITIC: OutcomeCriticReport = { ran: true, mount: "letter", advisories: [], dropped_citations: 0 };
const props: ReviewSurfaceProps = {
  documentKind: "cover-letter",
  documentId: "letter-1",
  atsReport: REPORT,
  truthReport: { version: "1", document_kind: "cover_letter", claims: [], counts: {}, stated_limit: "" },
  criticReport: CRITIC,
  gapClusters: null,
  hasClusterProducer: false,
};

// ADR-099 cl. 8: craft notes (the repeated employer name among them) left the
// review surface — a passing craft check is "S" (silent) and only counts in
// the passed line. 786:47's wording lives on in the catalog for the writer's
// own loop; the surface must not render it.
describe("786:47 under ADR-099 cl. 8 — the repeated employer name is not on the surface", () => {
  it("DE: no craft row, no EN details; the check counts as passed", () => {
    render(withIntl(<ReviewSurface {...props} />, "de"));
    const surface = screen.getByTestId("review-surface").textContent ?? "";
    expect(surface).not.toContain("Wiederholter Firmenname");
    expect(surface).not.toContain("Schwarzwald Präzision GmbH");
    expect(surface).not.toContain("opens 4 sentences");
    expect(screen.queryByTestId("review-other")).toBeNull();
    expect(screen.getByTestId("review-passed-checks").textContent).toContain("1");
  });

  it("EN: nothing of the note either", () => {
    render(withIntl(<ReviewSurface {...props} />, "en"));
    expect(screen.getByTestId("review-surface").textContent ?? "").not.toContain("Repeated");
  });
});
