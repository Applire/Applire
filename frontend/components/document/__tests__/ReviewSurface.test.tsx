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

import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { markDocumentWalked } from "@/lib/review-walked";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function ats(
  keywords: Partial<NonNullable<ATSReport>["keywords"]> = {},
  checks: NonNullable<ATSReport>["checks"] = [],
): ATSReport {
  return {
    checks,
    keywords: {
      present: ["Python"],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: [],
      claimable_concepts: [],
      ...keywords,
    },
  };
}

function truth(claims: NonNullable<TruthfulnessReport>["claims"] = []): TruthfulnessReport {
  return { version: "1", document_kind: "cv", claims, counts: {}, stated_limit: "" };
}

const CRITIC_RAN: OutcomeCriticReport = {
  ran: true,
  mount: "cv",
  advisories: [],
  dropped_citations: 0,
};

/** A document with something in EVERY group — the shape both modes must honour. */
const FULL_INPUTS: Omit<ReviewSurfaceProps, "modePreference"> = {
  documentKind: "cv",
  documentId: "generated-cv-1",
  atsReport: ats(
    {
      present_unsupported: ["Kubernetes"],
      missing_claimable: ["SAP PP"],
      missing_honest_gap: ["Terraform"],
      missing: ["SAP PP", "Terraform"],
    },
    [
      { id: "contact-0", status: "pass" },
      { id: "headings-0", status: "pass" },
      { id: "page-length-0", status: "fail", details: "3 Seiten" },
    ],
  ),
  truthReport: truth(),
  criticReport: CRITIC_RAN,
  gapClusters: [{ id: "g1", label: "Cloud-Betrieb", kind: "honest" }],
};

function renderSurface(over: Partial<ReviewSurfaceProps> = {}) {
  return render(
    withIntl(<ReviewSurface {...FULL_INPUTS} modePreference="overview" {...over} />),
  );
}

beforeEach(() => window.localStorage.clear());

describe("ADR-081 clause 4 — one verdict sentence, counting the rows it renders", () => {
  it("states group 1's count, and that number equals the group-1 rows actually rendered", () => {
    renderSurface({
      atsReport: ats({ present_unsupported: ["Kubernetes", "Terraform", "Rust"] }),
    });
    const sentence = screen.getByTestId("review-verdict").textContent ?? "";
    const stated = Number(sentence.match(/\d+/)?.[0]);
    const rendered = document.querySelectorAll('[data-testid^="review-item-g1-"]').length;
    expect(stated).toBe(3);
    expect(rendered).toBe(stated);
  });

  it("keeps groups 2-4 out of the headline's number", () => {
    renderSurface();
    const sentence = screen.getByTestId("review-verdict").textContent ?? "";
    expect(Number(sentence.match(/\d+/)?.[0])).toBe(1); // only Kubernetes
  });

  // JF-F-K.1 — one authoritative sentence is read as a summary OF the surface.
  it("does NOT render an unqualified all-clear while groups 2-4 carry findings", () => {
    renderSurface({ atsReport: ats({ missing_honest_gap: ["Terraform"], missing: ["Terraform"] }) });
    const sentence = screen.getByTestId("review-verdict").textContent ?? "";
    expect(sentence).toMatch(/further finding/i);
  });

  it("renders the plain all-clear only when every producer ran and every group is empty", () => {
    renderSurface({
      atsReport: ats({}, [{ id: "contact-0", status: "pass" }]),
      gapClusters: [],
    });
    const sentence = screen.getByTestId("review-verdict").textContent ?? "";
    expect(sentence).toMatch(/covered by your profile\.$/);
  });
});

/**
 * ADR-081 clause 6 / SF-REVIEW.2 / JF-F-K.2 — the visibility invariant.
 *
 * ONE TEST PER MODE, deliberately not a shared assertion: a shared one passes
 * on the mode it was written against, which is exactly the false green
 * `SF-REVIEW.2`'s mitigation cell warns about.
 */
describe("clause 6 — every non-zero group renders its count", () => {
  it("overview mode renders every non-zero group's count", () => {
    renderSurface({ modePreference: "overview" });
    for (const id of [1, 2, 3, 4]) {
      const badge = screen.getByTestId(`review-group-count-${id}`);
      expect(badge.textContent).not.toBe("");
      expect(Number(badge.textContent)).toBeGreaterThan(0);
    }
  });

  it("guided mode renders every non-zero group's count", () => {
    renderSurface({ modePreference: "guided" });
    expect(screen.getByTestId("review-guided-counts")).toBeTruthy();
    for (const id of [1, 2, 3, 4]) {
      const badge = screen.getByTestId(`review-group-count-${id}`);
      expect(badge.textContent).not.toBe("");
      expect(Number(badge.textContent)).toBeGreaterThan(0);
    }
  });

  it("guided mode always offers the one-click switch back to overview", () => {
    renderSurface({ modePreference: "guided" });
    const sw = screen.getByTestId("review-mode-switch");
    fireEvent.click(sw);
    expect(screen.getByTestId("review-overview")).toBeTruthy();
  });

  it("overview mode offers the switch into guided (two-way, one click either direction)", () => {
    renderSurface({ modePreference: "overview" });
    fireEvent.click(screen.getByTestId("review-mode-switch"));
    expect(screen.getByTestId("review-guided")).toBeTruthy();
  });

  it("collapses a group to a single line only at count zero", () => {
    renderSurface({
      atsReport: ats({ present_unsupported: ["Kubernetes"] }),
      gapClusters: [],
    });
    // Group 3 is empty here: its toggle is inert (nothing to expand).
    expect((screen.getByTestId("review-group-toggle-3") as HTMLButtonElement).disabled).toBe(true);
    // Group 1 is not: it has a finding and must be expandable AND open.
    expect((screen.getByTestId("review-group-toggle-1") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId("review-group-toggle-1").getAttribute("aria-expanded")).toBe("true");
  });

  it("opens the FIRST PRESENT group in overview, not merely the first group", () => {
    renderSurface({
      atsReport: ats({ missing_claimable: ["SAP PP"], missing: ["SAP PP"] }),
      gapClusters: [],
    });
    expect(screen.getByTestId("review-group-toggle-1").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("review-group-toggle-2").getAttribute("aria-expanded")).toBe("true");
  });

  it("collapses PASSING checks only, and only when the ATS report actually ran", () => {
    renderSurface({
      atsReport: ats({}, [
        { id: "contact-0", status: "pass" },
        { id: "headings-0", status: "pass" },
      ]),
      gapClusters: [],
    });
    expect(screen.getByTestId("review-passed-checks").textContent).toMatch(/2 checks passed/);
  });
});

/**
 * ADR-081 clause 9 / SF-REVIEW.4 / JF-F-K.4 — a producer that did not run.
 * Pinned PER PRODUCER: this is the test the FMEA row was narrowed down to.
 */
describe("clause 9 — a producer that did not run renders as unknown, never as zero", () => {
  it("Oracle absent: group 1 says so instead of showing a count", () => {
    renderSurface({ truthReport: null });
    expect(screen.getByTestId("review-group-unknown-1").textContent).toMatch(
      /Truthfulness Oracle did not run/,
    );
  });

  it("outcome critic absent (ADR-060 ran:false): group 4 says so", () => {
    renderSurface({ criticReport: { ran: false, advisories: [], dropped_citations: 0 } });
    expect(screen.getByTestId("review-group-unknown-4").textContent).toMatch(
      /Coherence advisory did not run/,
    );
  });

  it("gap clusters never loaded: group 3 says so", () => {
    renderSurface({ gapClusters: null });
    expect(screen.getByTestId("review-group-unknown-3").textContent).toMatch(
      /Gap analysis did not run/,
    );
  });

  it("ATS report absent: every group carries the unknown note and the verdict refuses an all-clear", () => {
    renderSurface({ atsReport: null, truthReport: null, criticReport: null, gapClusters: null });
    expect(screen.getByTestId("review-group-count-1").getAttribute("data-review-unknown")).toBe(
      "true",
    );
    expect(screen.getByTestId("review-group-count-1").textContent).toBe("unknown");
    expect(screen.getByTestId("review-verdict").textContent).toMatch(/was not checked/i);
  });

  it("keeps an unknown group OUT of the passed-checks collapse", () => {
    renderSurface({ atsReport: null, criticReport: null });
    expect(screen.queryByTestId("review-passed-checks")).toBeNull();
  });

  it("a producer that ran and found nothing renders 0, not unknown", () => {
    renderSurface({ atsReport: ats({}, []), gapClusters: [] });
    const badge = screen.getByTestId("review-group-count-3");
    expect(badge.textContent).toBe("0");
    expect(badge.getAttribute("data-review-unknown")).toBeNull();
  });

  it("declares a producer that does not APPLY (the letter has no gap clusters) rather than faking one", () => {
    renderSurface({ documentKind: "cover-letter", gapClusters: [], hasClusterProducer: false });
    expect(screen.queryByTestId("review-group-unknown-3")).toBeNull();
  });
});

/** ADR-081 clause 2 — the group-1 carve-out, at the rendered surface. */
describe("clause 2 — one row citing both producers, and nothing else suppressed", () => {
  it("renders a genuine overlap as ONE row naming both producers", () => {
    renderSurface({
      atsReport: ats({ present_unsupported: ["Lean-Management"] }),
      truthReport: truth([
        {
          claim: { text: "Lean–Management", location: "work-0", kind: "skill" },
          verdict: { verdict: "unbacked", checker: "literal", evidence: [], detail: null },
        },
      ]),
    });
    const rows = document.querySelectorAll('[data-testid^="review-item-g1-"]');
    expect(rows).toHaveLength(1);
    expect(within(rows[0] as HTMLElement).getAllByTestId(/review-item-producer-/)).toHaveLength(2);
  });

  it("does NOT collapse two findings that merely resemble each other", () => {
    renderSurface({
      atsReport: ats({ present_unsupported: ["SAP PP"] }),
      truthReport: truth([
        {
          claim: { text: "SAP PP/DS", location: "work-0", kind: "skill" },
          verdict: { verdict: "unbacked", checker: "literal", evidence: [], detail: null },
        },
      ]),
    });
    expect(document.querySelectorAll('[data-testid^="review-item-g1-"]')).toHaveLength(2);
  });

  it("labels each group-3 item with its origin instead of fusing the two granularities", () => {
    renderSurface({
      atsReport: ats({ missing_honest_gap: ["Terraform"], missing: ["Terraform"] }),
      gapClusters: [{ id: "g1", label: "Cloud-Betrieb", kind: "honest" }],
    });
    // Group 1 is empty here, so group 3 is the FIRST PRESENT group and is
    // already open (ADR-081 cl. 5) — clicking would close it.
    const group = screen.getByTestId("review-group-3");
    expect(within(group).getByText("Term")).toBeTruthy();
    expect(within(group).getByText("Cluster")).toBeTruthy();
  });
});

/** US302 / ADR-081 clause 3 — group 2 names the trade and offers no fix. */
describe("clause 3 — group 2 states the trade, with no action", () => {
  it("names the trade and the three existing handles", () => {
    renderSurface({
      atsReport: ats({ missing_claimable: ["SAP PP"], missing: ["SAP PP"] }),
      gapClusters: [],
    });
    const trade = screen.getByTestId("review-group2-trade");
    expect(trade.textContent).toMatch(/length trade/i);
    expect(screen.getByTestId("review-group2-handle-pages").textContent).toMatch(/page target/i);
    expect(screen.getByTestId("review-group2-handle-pin").textContent).toContain("Pin the fact behind it");
    expect(screen.getByTestId("review-group2-handle-pin").textContent).toContain("never cut");
    expect(screen.getByTestId("review-group2-handle-regenerate").textContent).toMatch(/regenerate/i);
  });

  it("renders NO control in group 2 that could write to the document", () => {
    renderSurface({
      atsReport: ats({ missing_claimable: ["SAP PP"], missing: ["SAP PP"] }),
      gapClusters: [{ id: "g2", label: "Produktionsplanung", kind: "claimable" }],
    });
    const group = screen.getByTestId("review-group-2");
    // The group's only button is its own collapse toggle.
    const buttons = within(group).getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("data-testid")).toBe("review-group-toggle-2");
  });

  it("links no fact pin as a finding's remedy — the pin is named as an existing handle only", () => {
    renderSurface({
      atsReport: ats({ missing_claimable: ["SAP PP"], missing: ["SAP PP"] }),
      gapClusters: [],
    });
    const group = screen.getByTestId("review-group-2");
    expect(within(group).queryByRole("link")).toBeNull();
  });
});

/** US301 / ADR-081 clause 5 + 5a — the modes, and the walked bit. */
describe("clause 5 — the mode follows the document under `auto`", () => {
  it("opens GUIDED on an unwalked document with group-1 findings", () => {
    renderSurface({ modePreference: "auto" });
    expect(screen.getByTestId("review-guided")).toBeTruthy();
  });

  it("opens OVERVIEW once this document has been walked", () => {
    markDocumentWalked("cv", "generated-cv-1");
    renderSurface({ modePreference: "auto" });
    expect(screen.getByTestId("review-overview")).toBeTruthy();
  });

  it("opens OVERVIEW when there is nothing send-blocking to walk", () => {
    renderSurface({
      modePreference: "auto",
      atsReport: ats({ missing_honest_gap: ["Terraform"], missing: ["Terraform"] }),
    });
    expect(screen.getByTestId("review-overview")).toBeTruthy();
  });

  // JF-F-K.3 — the regenerate-then-assert test the FMEA row demanded.
  it("REGENERATION returns to guided instead of inheriting walked=true", () => {
    markDocumentWalked("cv", "generated-cv-1");
    const { unmount } = renderSurface({ modePreference: "auto" });
    expect(screen.getByTestId("review-overview")).toBeTruthy();
    unmount();

    // Same application, same findings, a NEW generated document.
    renderSurface({ modePreference: "auto", documentId: "generated-cv-2" });
    expect(screen.getByTestId("review-guided")).toBeTruthy();
  });

  it("marks the document walked once the reader is past the last group-1 finding", () => {
    renderSurface({ modePreference: "auto" });
    // One group-1 finding: one Next takes us past it.
    fireEvent.click(screen.getByTestId("review-guided-next"));
    expect(window.localStorage.getItem("applire.review.walked.cv.generated-cv-1")).toBe("1");
  });

  it("shows how many findings REMAIN, not `n of N` (JF-F-K.2)", () => {
    renderSurface({ modePreference: "guided" });
    const text = screen.getByTestId("review-guided-remaining").textContent ?? "";
    expect(text).toMatch(/still unread/);
    expect(text).not.toMatch(/\d+\s*(of|von)\s*\d+/);
  });

  it("switching the view never writes the stored preference", () => {
    renderSurface({ modePreference: "overview" });
    fireEvent.click(screen.getByTestId("review-mode-switch"));
    // No settings PATCH is issued by this component at all.
    expect(window.localStorage.getItem("applire.review.mode")).toBeNull();
  });
});
