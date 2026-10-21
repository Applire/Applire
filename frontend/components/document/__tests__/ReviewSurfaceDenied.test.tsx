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

// Build-2 delivery F2 (SF-REVIEW.18): the letter review offered "Stimmt, in mein
// Profil aufnehmen" on "Lebensmittelkunden", which the candidate's own recorded
// denial covers. A term the report says a denial covers shows that denial and no
// add-to-profile handle; take-out and edit stay.

import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const DENIAL = "direkte Produktion für Lebensmittelkunden";

function ats(denials: Record<string, string[]> | undefined): ATSReport {
  return {
    checks: [],
    keywords: {
      present: ["Lebensmittelkunden", "Payments Platform"],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: ["Lebensmittelkunden", "Payments Platform"],
      present_unsupported_matches: {
        Lebensmittelkunden: [{ form: "Lebensmittelkunden", stem: false }],
        "Payments Platform": [{ form: "Payments", stem: false }],
      },
      present_unsupported_denials: denials,
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

describe("F2 — a term a recorded denial covers", () => {
  it("shows the denial instead of the add-to-profile handle (de)", () => {
    render(withIntl(<ReviewSurface {...props(ats({ Lebensmittelkunden: [DENIAL] }))} />, "de"));
    const card = screen.getByTestId("review-card");
    expect(card.getAttribute("data-finding-key")).toBe("ats:lebensmittelkunden");
    expect(within(card).queryByTestId("review-action-add")).toBeNull();
    expect(within(card).getByTestId("review-card-denied").textContent).toBe(
      "Dazu hast Du selbst eine Einschränkung genannt: „direkte Produktion für Lebensmittelkunden“. " +
        "Deshalb bietet Applire nicht an, es in Dein Profil aufzunehmen. Nimm es heraus oder bearbeite die Stelle.",
    );
    expect(within(card).getByTestId("review-action-takeout")).toBeTruthy();
    expect(within(card).getByTestId("review-action-edit")).toBeTruthy();
  });

  it("says the same in English", () => {
    render(withIntl(<ReviewSurface {...props(ats({ Lebensmittelkunden: [DENIAL] }))} />, "en"));
    expect(screen.getByTestId("review-card-denied").textContent).toBe(
      "You told us about a limit here yourself: “direkte Produktion für Lebensmittelkunden”. " +
        "That is why Applire does not offer to add it to your profile. Take it out or edit the passage.",
    );
  });

  it("keeps the add handle on a term no denial covers", () => {
    render(withIntl(<ReviewSurface {...props(ats({ Lebensmittelkunden: [DENIAL] }))} />, "en"));
    const rows = document.querySelectorAll('[data-testid^="review-item-g1-"][data-status="open"]');
    fireEvent.click(rows[1]);
    const card = screen.getByTestId("review-card");
    expect(card.getAttribute("data-finding-key")).toBe("ats:payments platform");
    expect(within(card).getByTestId("review-action-add")).toBeTruthy();
    expect(within(card).queryByTestId("review-card-denied")).toBeNull();
  });

  it("a report without the field keeps today's card", () => {
    render(withIntl(<ReviewSurface {...props(ats(undefined))} />, "en"));
    expect(within(screen.getByTestId("review-card")).getByTestId("review-action-add")).toBeTruthy();
  });
});
