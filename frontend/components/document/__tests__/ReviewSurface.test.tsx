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

import React from "react";
import { render, screen, fireEvent, within, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import type { ReviewState } from "@/lib/api/document-review";
import { makePreviewLocator } from "@/lib/locate-in-preview";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Contract 2 — the review endpoints, mocked until WP-A's branch lands.
const api = vi.hoisted(() => ({
  addEvidence: vi.fn(),
  takeOut: vi.fn(),
  undoDecision: vi.fn(),
  markEdited: vi.fn(),
  markWalked: vi.fn(),
}));
vi.mock("@/lib/api/document-review", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/document-review")>();
  return { ...actual, ...api };
});

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

const CRITIC_RAN: OutcomeCriticReport = { ran: true, mount: "cv", advisories: [], dropped_citations: 0 };

/** A document with something in EVERY group. */
const FULL_INPUTS: ReviewSurfaceProps = {
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
  gapAnalysisHref: "/flow/f1/gaps",
};

function renderSurface(over: Partial<ReviewSurfaceProps> = {}) {
  return render(withIntl(<ReviewSurface {...FULL_INPUTS} {...over} />));
}

/** The OPEN group-1 rows rendered. */
const openRows = () => document.querySelectorAll('[data-testid^="review-item-g1-"][data-status="open"]');
const verdictNumber = () => Number(screen.getByTestId("review-verdict").textContent?.match(/\d+/)?.[0]);

// A preview document holding "AI governance" twice (one across an inline element).
function previewDoc(): Document {
  const doc = document.implementation.createHTMLDocument("cv");
  const p = doc.createElement("p");
  p.append("Focus on data quality and ");
  const b = doc.createElement("b");
  b.textContent = "AI";
  p.append(b, " governance in regulated industries.");
  const li = doc.createElement("li");
  li.textContent = "Built the group-wide AI governance board";
  doc.body.append(p, li);
  return doc;
}

const MATCHED_ATS = ats({
  present_unsupported: ["IT Data & AI Governance", "Collibra", "Snowflake"],
  present_unsupported_matches: {
    "IT Data & AI Governance": [{ form: "AI governance", stem: false }],
    Collibra: [{ form: "Collibra", stem: false }],
  },
});

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.markWalked.mockResolvedValue({ review_state: null });
});

/* ------------------------------------------------------------------ cl. 1 */

describe("ADR-090 cl. 1 — one layout, no mode switch", () => {
  it("renders no overview/guided switch and no 'still unread' counter", () => {
    renderSurface();
    expect(screen.queryByTestId("review-mode-switch")).toBeNull();
    expect(screen.queryByTestId("review-guided")).toBeNull();
    expect(screen.queryByText(/still unread/i)).toBeNull();
  });

  it("group 1 is a list of buttons with the first open finding as the current card", () => {
    renderSurface({ atsReport: MATCHED_ATS });
    const rows = Array.from(openRows());
    expect(rows).toHaveLength(3);
    rows.forEach((r) => expect(r.tagName).toBe("BUTTON"));
    expect(screen.getByTestId("review-card").getAttribute("data-finding-key")).toBe("ats:it data & ai governance");
    expect(screen.getByTestId("review-card-position").textContent).toBe("1 OF 3");
    // Every row opens its card.
    fireEvent.click(rows[2]);
    expect(screen.getByTestId("review-card").getAttribute("data-finding-key")).toBe("ats:snowflake");
    expect(screen.getByTestId("review-card-position").textContent).toBe("3 OF 3");
  });

  it("the card offers the four handles", () => {
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(previewDoc) });
    const card = screen.getByTestId("review-card");
    expect(within(card).getByTestId("review-action-locate").textContent).toBe("Show me where");
    expect(within(card).getByTestId("review-action-add").textContent).toBe("It’s true, add it to my profile");
    expect(within(card).getByTestId("review-action-takeout").textContent).toBe("Take it out for me");
    expect(within(card).getByTestId("review-action-edit").textContent).toBe("Let me edit it");
  });

  it("the group-3 row links to the gap view", () => {
    renderSurface();
    expect(screen.getByTestId("review-group3-link").getAttribute("href")).toBe("/flow/f1/gaps");
    expect(screen.getByTestId("review-group3-link").textContent).toContain("Open in the gap analysis");
  });

  it("the verdict carries the instruction line and the coverage line reads from the report", () => {
    renderSurface();
    expect(screen.getByTestId("review-verdict-sub").textContent).toBe(
      "Check each one. Keep it if it is true, take it out if it is not.",
    );
    expect(screen.getByTestId("review-coverage").textContent).toBe("1 of 3 job keywords appear in the document.");
  });
});

/* -------------------------------------------- cl. 4 as amended by cl. 6 */

describe("ADR-081 cl. 4 (amended by ADR-090 cl. 6) — the verdict counts the OPEN rows it renders", () => {
  it("states the open group-1 count, equal to the open rows rendered", () => {
    renderSurface({ atsReport: ats({ present_unsupported: ["Kubernetes", "Terraform", "Rust"] }) });
    expect(verdictNumber()).toBe(3);
    expect(openRows()).toHaveLength(3);
  });

  it("a decided finding the report no longer lists leaves the verdict but counts in n", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: "ats:data mesh", label: "Data Mesh", action: "taken_out", at: "2026-09-23T10:00:00Z", undo: null }],
    };
    renderSurface({ atsReport: ats({ present_unsupported: ["Kubernetes", "Terraform"] }), reviewState: state });
    expect(verdictNumber()).toBe(2);
    expect(openRows()).toHaveLength(2);
    expect(screen.getByTestId("review-progress-label").textContent).toBe("1 of 3 decided");
    expect(screen.getByTestId("review-row-status-taken_out").textContent).toBe("Taken out");
  });

  // SF-REVIEW.9 — the mutation this kills: trusting `review_state` over the live report.
  it("a decision NEVER hides a finding the live report still lists", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: "ats:kubernetes", label: "Kubernetes", action: "taken_out", at: "2026-09-23T10:00:00Z", undo: null }],
    };
    renderSurface({ atsReport: ats({ present_unsupported: ["Kubernetes"] }), reviewState: state });
    expect(verdictNumber()).toBe(1);
    expect(openRows()).toHaveLength(1);
    expect(screen.queryByTestId("review-row-status-taken_out")).toBeNull();
    expect(screen.getByTestId("review-progress-label").textContent).toBe("0 of 1 decided");
  });

  it("keeps groups 2-4 out of the headline's number", () => {
    renderSurface();
    expect(verdictNumber()).toBe(1);
  });

  it("does NOT render an unqualified all-clear while groups 2-4 carry findings (JF-F-K.1)", () => {
    renderSurface({ atsReport: ats({ missing_honest_gap: ["Terraform"], missing: ["Terraform"] }) });
    expect(screen.getByTestId("review-verdict").textContent).toMatch(/further finding/i);
  });

  it("renders the plain all-clear only when every producer ran and every group is empty", () => {
    renderSurface({ atsReport: ats({}, [{ id: "contact-0", status: "pass" }]), gapClusters: [] });
    expect(screen.getByTestId("review-verdict").textContent).toMatch(/covered by your profile\.$/);
  });
});

/* ------------------------------------------------------------------ cl. 6 */

/**
 * ADR-081 clause 6 / SF-REVIEW.2 — the visibility invariant, on the ONE layout.
 * The mutation it kills: rendering a group's count only when it is opened.
 */
describe("ADR-081 cl. 6 — every non-zero group's count is visible without interaction", () => {
  it("renders every non-zero group's count on first paint", () => {
    renderSurface();
    // Group 1's number is the verdict (and the tab badge); groups 2-4 are rows.
    expect(verdictNumber()).toBe(1);
    for (const id of [2, 3, 4]) {
      const badge = screen.getByTestId(`review-group-count-${id}`);
      expect(Number(badge.textContent)).toBeGreaterThan(0);
      expect(badge.closest('[aria-expanded="true"]')).toBeNull();
    }
  });

  it("collapses a group to a single inert line only at count zero", () => {
    renderSurface({ atsReport: ats({ present_unsupported: ["Kubernetes"] }), gapClusters: [], criticReport: CRITIC_RAN });
    expect((screen.getByTestId("review-group-toggle-2") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("review-group-count-2").textContent).toBe("0");
  });

  it("a non-zero group 2 or 4 opens in place", () => {
    renderSurface();
    const toggle = screen.getByTestId("review-group-toggle-4");
    expect((toggle as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(within(screen.getByTestId("review-group-4")).getByText("3 Seiten")).toBeTruthy();
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

/* ------------------------------------------------------------------ cl. 9 */

/**
 * ADR-081 clause 9 / SF-REVIEW.4 on the new layout. The mutation it kills:
 * dropping the unknown note above the verdict, or showing a complete-looking
 * progress figure while a group-1 producer is blind.
 */
describe("ADR-081 cl. 9 — a producer that did not run renders as unknown, never as zero", () => {
  it("Oracle absent: the note stands ABOVE the verdict and the list, and no progress figure is shown", () => {
    renderSurface({ truthReport: null });
    const note = screen.getByTestId("review-group-unknown-1");
    expect(note.textContent).toMatch(/Truthfulness Oracle did not run/);
    const verdict = screen.getByTestId("review-verdict");
    const list = screen.getByTestId("review-group1-list");
    expect(note.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(note.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("review-progress")).toBeNull();
  });

  it("both group-1 producers absent: the verdict says unchecked, never an all-clear or a zero", () => {
    renderSurface({ atsReport: null, truthReport: null, criticReport: null, gapClusters: null });
    expect(screen.getByTestId("review-verdict").textContent).toMatch(/was not checked/i);
    expect(screen.getByTestId("review-group-unknown-1").textContent).toMatch(/Not checked/);
    expect(screen.queryByTestId("review-progress")).toBeNull();
    expect(screen.getByTestId("review-group-count-2").textContent).toBe("unknown");
  });

  it("outcome critic absent (ADR-060 ran:false): group 4 says so, without interaction", () => {
    renderSurface({ criticReport: { ran: false, advisories: [], dropped_citations: 0 } });
    expect(screen.getByTestId("review-group-unknown-4").textContent).toMatch(/Coherence advisory did not run/);
  });

  it("gap clusters never loaded: group 3 says so", () => {
    renderSurface({ gapClusters: null });
    expect(screen.getByTestId("review-group-unknown-3").textContent).toMatch(/Gap analysis did not run/);
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

/* ------------------------------------------------------------------ cl. 2 */

describe("ADR-081 cl. 2 — one row citing both producers, and nothing else suppressed", () => {
  it("renders a genuine overlap as ONE row whose card names both producers", () => {
    renderSurface({
      atsReport: ats({ present_unsupported: ["Lean-Management"] }),
      truthReport: truth([
        {
          claim: { text: "Lean–Management", location: "work-0", kind: "skill" },
          verdict: { verdict: "unbacked", checker: "literal", evidence: [], detail: null },
        },
      ]),
    });
    expect(openRows()).toHaveLength(1);
    expect(within(screen.getByTestId("review-card")).getAllByTestId(/review-item-producer-/)).toHaveLength(2);
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
    expect(openRows()).toHaveLength(2);
  });
});

describe("ADR-081 cl. 3 — group 2 states the trade, with no action", () => {
  it("names the trade and the existing handles once opened", () => {
    renderSurface({ atsReport: ats({ missing_claimable: ["SAP PP"], missing: ["SAP PP"] }), gapClusters: [] });
    fireEvent.click(screen.getByTestId("review-group-toggle-2"));
    expect(screen.getByTestId("review-group2-trade").textContent).toMatch(/length trade/i);
    expect(screen.getByTestId("review-group2-handle-pin").textContent).toContain("Pin the fact behind it");
  });

  it("renders NO control in group 2 that could write to the document", () => {
    renderSurface({
      atsReport: ats({ missing_claimable: ["SAP PP"], missing: ["SAP PP"] }),
      gapClusters: [{ id: "g2", label: "Produktionsplanung", kind: "claimable" }],
    });
    const group = screen.getByTestId("review-group-2");
    fireEvent.click(screen.getByTestId("review-group-toggle-2"));
    // Without the section-editor handler the only button is the group's toggle.
    const buttons = within(group).getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(within(group).queryByRole("link")).toBeNull();
  });
});

/* ------------------------------------------------------ cl. 2 — locate */

describe("ADR-090 cl. 2 — Show me where", () => {
  it("names the document's own wording and the number of places", () => {
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(previewDoc) });
    expect(screen.getByTestId("review-card-matched").textContent).toBe(
      "Your document says “AI governance” in 2 places. Nothing in your profile backs it.",
    );
  });

  it("marks every place, steps between them, and emphasises the current one", () => {
    const doc = previewDoc();
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(() => doc) });
    fireEvent.click(screen.getByTestId("review-action-locate"));
    expect(doc.querySelectorAll("mark[data-applire-hit]").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId("review-locate-stepper").textContent).toContain("Showing place 1 of 2");
    expect((screen.getByTestId("review-locate-prev") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("review-locate-next"));
    expect(screen.getByTestId("review-locate-stepper").textContent).toContain("Showing place 2 of 2");
    expect(doc.querySelector("mark[data-applire-current]")!.getAttribute("data-applire-hit")).toBe("1");
    expect((screen.getByTestId("review-locate-next") as HTMLButtonElement).disabled).toBe(true);
  });

  it("opening another card removes the marks", () => {
    const doc = previewDoc();
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(() => doc) });
    fireEvent.click(screen.getByTestId("review-action-locate"));
    fireEvent.click(screen.getByTestId("review-item-g1-ats:collibra"));
    expect(doc.querySelectorAll("mark")).toHaveLength(0);
  });

  it("no matched forms in the report → the card says so and still offers the other handles", () => {
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(previewDoc) });
    fireEvent.click(screen.getByTestId("review-item-g1-ats:snowflake"));
    expect(screen.getByTestId("review-locate-not-found").textContent).toBe(
      "This place could not be marked in the preview.",
    );
    expect(screen.queryByTestId("review-action-locate")).toBeNull();
    expect(screen.getByTestId("review-action-takeout")).toBeTruthy();
    expect(screen.getByTestId("review-action-add")).toBeTruthy();
    expect(screen.getByTestId("review-action-edit")).toBeTruthy();
  });

  it("matched forms that the preview does not hold → could not be marked (never a silent locate)", () => {
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(previewDoc) });
    fireEvent.click(screen.getByTestId("review-item-g1-ats:collibra"));
    expect(screen.getByTestId("review-locate-not-found")).toBeTruthy();
    expect(screen.queryByTestId("review-card-matched")).toBeNull();
  });

  it("phone: Show me where switches to the locate view and Back to review returns to the card", () => {
    const onLocateModeChange = vi.fn();
    const doc = previewDoc();
    renderSurface({
      atsReport: MATCHED_ATS,
      locator: makePreviewLocator(() => doc),
      layout: "sheet",
      onLocateModeChange,
    });
    fireEvent.click(screen.getByTestId("review-action-locate"));
    expect(onLocateModeChange).toHaveBeenLastCalledWith(true);
    const view = screen.getByTestId("review-phone-locate");
    expect(within(view).getByTestId("review-locate-place").textContent).toBe("“AI governance” · place 1 of 2");
    expect(within(view).getByTestId("review-handles-grid")).toBeTruthy();
    fireEvent.click(within(view).getByTestId("review-locate-back"));
    expect(onLocateModeChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByTestId("review-phone-locate")).toBeNull();
    expect(doc.querySelectorAll("mark")).toHaveLength(0);
  });
});

/* ------------------------------------------------------ cl. 3 — take out */

describe("ADR-090 cl. 3 — Take it out for me", () => {
  const AFTER = ats({
    present_unsupported: ["Collibra", "Snowflake"],
    present_unsupported_matches: { Collibra: [{ form: "Collibra", stem: false }] },
  });
  const TAKEN_STATE: ReviewState = {
    walked_at: null,
    decisions: [
      {
        finding_key: "ats:it data & ai governance",
        label: "IT Data & AI Governance",
        action: "taken_out",
        at: "2026-09-23T10:00:00Z",
        undo: { sections: [{ section_id: "summary", before: "…" }] },
      },
    ],
  };

  function Harness(props: Partial<ReviewSurfaceProps>) {
    // The page's role: apply the refreshed report and state from the response.
    const [report, setReport] = React.useState<ATSReport>(MATCHED_ATS);
    const [state, setState] = React.useState<ReviewState | null>(null);
    return (
      <ReviewSurface
        {...FULL_INPUTS}
        atsReport={report}
        reviewState={state}
        locator={makePreviewLocator(previewDoc)}
        sectionLabel={(id) => (id === "summary" ? "Profile" : id)}
        onRefresh={(r) => {
          if (r.report !== undefined) setReport(r.report);
          setState(r.review_state);
        }}
        {...props}
      />
    );
  }

  it("shows what changed, labels the row, counts it decided, and offers Undo", async () => {
    api.takeOut.mockResolvedValue({
      changes: [
        {
          section_id: "summary",
          before: "Focus on data quality and AI governance in regulated industries.",
          after: "Focus on data quality in regulated industries.",
        },
        {
          section_id: "work-0",
          before: "Built the group-wide AI governance board and the review process.",
          after: "Built the review process.",
        },
      ],
      still_listed: false,
      report: AFTER,
      truthfulness: null,
      review_state: TAKEN_STATE,
    });
    render(withIntl(<Harness />));
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getByTestId("review-takeout-done")).toBeTruthy());
    expect(api.takeOut).toHaveBeenCalledWith("cv", "generated-cv-1", "ats:it data & ai governance");
    expect(screen.getByTestId("review-takeout-done").textContent).toBe("Taken out in 2 places. This is what changed:");
    expect(screen.getAllByTestId("review-takeout-change")).toHaveLength(2);
    expect(screen.getAllByTestId("review-takeout-change")[0].textContent).toContain("Profile");
    expect(screen.getByTestId("review-action-undo").textContent).toBe("Undo");
    expect(screen.getByTestId("review-action-next").textContent).toBe("Next finding");
    expect(screen.getByTestId("review-row-status-taken_out")).toBeTruthy();
    expect(verdictNumber()).toBe(2);
    expect(screen.getByTestId("review-progress-label").textContent).toBe("1 of 3 decided");

    // Next finding → the next open one.
    fireEvent.click(screen.getByTestId("review-action-next"));
    expect(screen.getByTestId("review-card").getAttribute("data-finding-key")).toBe("ats:collibra");
  });

  it("still listed after the rewrite → the card says so and offers Let me edit it", async () => {
    api.takeOut.mockResolvedValue({
      changes: [{ section_id: "summary", before: "a AI governance b", after: "a AI-Governance b" }],
      still_listed: true,
      report: MATCHED_ATS,
      truthfulness: null,
      review_state: TAKEN_STATE,
    });
    render(withIntl(<Harness />));
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getByTestId("review-takeout-still")).toBeTruthy());
    expect(screen.getByTestId("review-takeout-still").textContent).toBe(
      "The check still finds this in the document. Edit it yourself to remove it.",
    );
    expect(screen.getByTestId("review-action-edit")).toBeTruthy();
    expect(screen.getByTestId("review-card").getAttribute("data-status")).toBe("open");
    expect(verdictNumber()).toBe(3);
  });

  it("Undo restores and the finding is open again", async () => {
    api.takeOut.mockResolvedValue({
      changes: [{ section_id: "summary", before: "AI governance", after: "" }],
      still_listed: false,
      report: AFTER,
      truthfulness: null,
      review_state: TAKEN_STATE,
    });
    api.undoDecision.mockResolvedValue({ report: MATCHED_ATS, truthfulness: null, review_state: { walked_at: null, decisions: [] } });
    const onRefresh = vi.fn();
    function Spy() {
      return <Harness />;
    }
    render(withIntl(<Spy />));
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getByTestId("review-action-undo")).toBeTruthy());
    fireEvent.click(screen.getByTestId("review-action-undo"));
    await waitFor(() => expect(screen.getByTestId("review-card").getAttribute("data-status")).toBe("open"));
    expect(api.undoDecision).toHaveBeenCalledWith("cv", "generated-cv-1", "ats:it data & ai governance");
    expect(verdictNumber()).toBe(3);
    void onRefresh;
  });

  it("a failed request says so on the card and changes nothing", async () => {
    const { ReviewActionError } = await import("@/lib/api/document-review");
    api.takeOut.mockRejectedValue(new ReviewActionError(502));
    render(withIntl(<Harness />));
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getByTestId("review-action-error")).toBeTruthy());
    expect(screen.getByTestId("review-action-error").textContent).toBe("An error occurred (502). Please try again.");
    expect(verdictNumber()).toBe(3);
  });

  it("tells the page the document changed so it reloads the preview", async () => {
    api.takeOut.mockResolvedValue({ changes: [{ section_id: "summary", before: "x", after: "y" }], still_listed: false, report: AFTER, truthfulness: null, review_state: TAKEN_STATE });
    const onRefresh = vi.fn();
    renderSurface({ atsReport: MATCHED_ATS, onRefresh });
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(onRefresh.mock.calls[0][1]).toEqual({ documentChanged: true });
  });
});

/* ---------------------------------------------------- cl. 4 — add evidence */

describe("ADR-090 cl. 4 — It's true, add it to my profile", () => {
  it("opens the box, submits the user's own words, and labels the finding Added", async () => {
    api.addEvidence.mockResolvedValue({
      testimony: { status: "applied", changes: [{}] },
      report: ats({ present_unsupported: ["Collibra", "Snowflake"] }),
      truthfulness: null,
      review_state: {
        walked_at: null,
        decisions: [
          { finding_key: "ats:it data & ai governance", label: "IT Data & AI Governance", action: "added", at: "2026-09-23T10:00:00Z", undo: null },
        ],
      },
    });
    function Harness() {
      const [report, setReport] = React.useState<ATSReport>(MATCHED_ATS);
      const [state, setState] = React.useState<ReviewState | null>(null);
      return (
        <ReviewSurface
          {...FULL_INPUTS}
          atsReport={report}
          reviewState={state}
          onRefresh={(r) => {
            if (r.report !== undefined) setReport(r.report);
            setState(r.review_state);
          }}
        />
      );
    }
    render(withIntl(<Harness />));
    fireEvent.click(screen.getByTestId("review-action-add"));
    const box = screen.getByTestId("review-add-box");
    expect(within(box).getByText("Where did you do this? In your own words.")).toBeTruthy();
    expect((screen.getByTestId("review-add-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("review-add-text"), {
      target: { value: "I chaired the AI governance board at Nordwerk." },
    });
    fireEvent.click(screen.getByTestId("review-add-submit"));
    await waitFor(() => expect(screen.getByTestId("review-row-status-added")).toBeTruthy());
    expect(api.addEvidence).toHaveBeenCalledWith(
      "cv",
      "generated-cv-1",
      "ats:it data & ai governance",
      "I chaired the AI governance board at Nordwerk.",
    );
    expect(screen.getByTestId("review-row-status-added").textContent).toBe("Added to profile");
    expect(screen.getByTestId("review-testimony-result").textContent).toBe("1 change saved to your profile.");
    expect(verdictNumber()).toBe(2);
  });

  it("a testimony that needs confirmation is shown on the card and the finding stays open", async () => {
    api.addEvidence.mockResolvedValue({
      testimony: { status: "needs_confirmation", changes: [] },
      report: MATCHED_ATS,
      truthfulness: null,
      review_state: null,
    });
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-action-add"));
    fireEvent.change(screen.getByTestId("review-add-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("review-add-submit"));
    await waitFor(() => expect(screen.getByTestId("review-testimony-result")).toBeTruthy());
    expect(screen.getByTestId("review-testimony-result").getAttribute("data-status")).toBe("needs_confirmation");
    expect(screen.getByTestId("review-card").getAttribute("data-status")).toBe("open");
  });

  it("Cancel closes the box without a request", () => {
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-action-add"));
    fireEvent.click(screen.getByTestId("review-add-cancel"));
    expect(screen.queryByTestId("review-add-box")).toBeNull();
    expect(api.addEvidence).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------- cl. 5 — edit */

describe("ADR-090 cl. 5 — Let me edit it", () => {
  it("hands the page the finding, its targets and the place being shown", () => {
    const onEditFinding = vi.fn();
    const doc = previewDoc();
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(() => doc), onEditFinding });
    fireEvent.click(screen.getByTestId("review-action-locate"));
    fireEvent.click(screen.getByTestId("review-locate-next"));
    fireEvent.click(screen.getByTestId("review-action-edit"));
    expect(onEditFinding).toHaveBeenCalledWith({
      findingKey: "ats:it data & ai governance",
      label: "IT Data & AI Governance",
      targets: [{ form: "AI governance", stem: false }],
      placeIndex: 1,
    });
  });
});

/* ------------------------------------------------------- walked_at */

describe("ADR-090 cl. 6 — walked_at replaces the browser-local walked bit", () => {
  it("marks the document walked once the last open finding is decided", async () => {
    api.takeOut.mockResolvedValue({
      changes: [{ section_id: "summary", before: "Kubernetes", after: "" }],
      still_listed: false,
      report: ats({ present_unsupported: [] }),
      truthfulness: null,
      review_state: { walked_at: null, decisions: [] },
    });
    renderSurface({ atsReport: ats({ present_unsupported: ["Kubernetes"] }) });
    await act(async () => {
      fireEvent.click(screen.getByTestId("review-action-takeout"));
    });
    await waitFor(() => expect(api.markWalked).toHaveBeenCalledWith("cv", "generated-cv-1"));
  });

  it("never touches localStorage", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem");
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-item-g1-ats:collibra"));
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

