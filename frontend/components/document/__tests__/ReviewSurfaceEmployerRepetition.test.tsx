// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// 786:47 (founder 2026-10-22, Hazelnut build 4): the letter audit's
// `employer-repetition` advisory reaches the review surface in the UI language,
// as an info row in group 4 — never counted in the headline.

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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

function openGroup4() {
  const toggle = screen.getByTestId("review-group-toggle-4");
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

describe("786:47 — the repeated employer name, in the user's words", () => {
  it("DE: label and sentence in German, no EN details", () => {
    render(withIntl(<ReviewSurface {...props} />, "de"));
    openGroup4();
    const text = screen.getByTestId("review-other").textContent ?? "";
    expect(text).toContain("Wiederholter Firmenname");
    expect(text).toContain(
      "„Schwarzwald Präzision GmbH“ steht in Absatz 2 am Anfang von 4 Sätzen (und in einem weiteren Absatz).",
    );
    expect(text).not.toContain("opens 4 sentences");
  });

  it("EN: the English sentence; a single paragraph has no rest clause", () => {
    render(
      withIntl(
        <ReviewSurface
          {...props}
          atsReport={{
            ...REPORT,
            checks: [{ ...REPORT.checks[0], details_params: { employer: "Alpha GmbH", paragraph: 1, count: 2, more: 0 } }],
          }}
        />,
        "en",
      ),
    );
    openGroup4();
    expect(screen.getByTestId("review-other").textContent).toContain("“Alpha GmbH” opens 2 sentences in paragraph 1. Repeated");
  });
});
