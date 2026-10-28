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
import { render, screen, fireEvent, within, waitFor, act, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import enMessages from "@/messages/en.json";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { refreshedReport, type ReviewState } from "@/lib/api/document-review";
import { makePreviewLocator } from "@/lib/locate-in-preview";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Contract 2 — the review endpoints, mocked until WP-A's branch lands.
const api = vi.hoisted(() => ({
  addEvidence: vi.fn(),
  confirmSupported: vi.fn(),
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
  // #797: the profile does not hold the wording unless a test says so → the ask opens.
  api.confirmSupported.mockResolvedValue(null);
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
    expect(screen.getByTestId("review-card-position").textContent).toMatch(/^1 OF 3/);
    // Every row opens its card.
    fireEvent.click(rows[2]);
    expect(screen.getByTestId("review-card").getAttribute("data-finding-key")).toBe("ats:snowflake");
    expect(screen.getByTestId("review-card-position").textContent).toMatch(/^3 OF 3/);
  });

  it("an unclassified card offers Show me where, Stimmt and Take out — no editor handle (ruling C-1 (2))", () => {
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(previewDoc) });
    const card = screen.getByTestId("review-card");
    expect(within(card).getByTestId("review-action-locate").textContent).toBe("Show me where");
    expect(within(card).getByTestId("review-action-add").textContent).toBe("It’s true, add it to my profile");
    expect(within(card).getByTestId("review-action-takeout").textContent).toBe("Take it out for me");
    expect(within(card).queryByTestId("review-action-edit")).toBeNull();
  });

  it("R2-10: the group-3 count is the gaps page's open-requirement count, not the ATS keyword misses", () => {
    renderSurface({
      atsReport: { ...MATCHED_ATS, keywords: { ...MATCHED_ATS!.keywords, missing_honest_gap: ["E-commerce", "Messaging", "Settlement"] } },
      openRequirements: ["FastAPI", "Apache Kafka"],
    });
    expect(screen.getByTestId("review-group-count-3").textContent).toBe("2");
  });

  it("the group-3 row links to the gap view", () => {
    renderSurface();
    expect(screen.getByTestId("review-group3-link").getAttribute("href")).toBe("/flow/f1/gaps");
    expect(screen.getByTestId("review-group3-link").textContent).toContain("Open in the gap analysis");
  });

  it("ADR-099 cl. 3: the verdict carries the one-question line; no coverage line, no progress bar", () => {
    renderSurface();
    expect(screen.getByTestId("review-verdict-sub").textContent).toBe(
      "Each point asks you one question. Your answer settles it – then Applire checks the document again.",
    );
    expect(screen.queryByTestId("review-coverage")).toBeNull();
    expect(screen.queryByTestId("review-progress")).toBeNull();
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
    // One row → the card alone (ruling C-1 (1): the list shows from two rows).
    expect(screen.getByTestId("review-card").getAttribute("data-finding-key")).toBe("ats:kubernetes");
    expect(screen.getByTestId("review-card").getAttribute("data-status")).toBe("open");
    expect(screen.queryByTestId("review-row-status-taken_out")).toBeNull();
  });

  it("keeps groups 2-4 out of the headline's number", () => {
    renderSurface();
    expect(verdictNumber()).toBe(1);
  });

  // ADR-099 cl. 3: the headline counts A + U only. Open requirements are the
  // uncounted group-3 link (the approved mock); a claimable term (old group 2)
  // is not on the surface; an unclassified failed check is the counted fallback.
  it("ADR-099: open requirements alone leave the clear line, with the group-3 link beside it", () => {
    renderSurface({ atsReport: ats({ missing_honest_gap: ["Terraform"], missing: ["Terraform"] }) });
    const head = screen.getByTestId("review-verdict");
    expect(head.textContent).toBe("Nothing needs you any more – the document is ready to send.");
    expect(head.getAttribute("data-count")).toBe("0");
    expect(screen.getByTestId("review-group3-link")).toBeTruthy();
  });

  it("ADR-099: leads with the count of what needs the user — the unclassified failed check, not the trade", () => {
    renderSurface({
      atsReport: ats(
        { missing_claimable: ["Scrum", "Jira"], missing_honest_gap: ["SAP"], missing: ["Scrum", "Jira", "SAP"] },
        [{ id: "contact-name", status: "fail" }],
      ),
      gapClusters: [],
    });
    const head = screen.getByTestId("review-verdict");
    expect(head.textContent).toBe("1 point needs you before you send.");
    expect(head.className).toContain("font-bold");
    expect(screen.getByTestId("review-fallback-contact-name").dataset.counted).toBe("true");
    expect(screen.queryByText("Scrum")).toBeNull();
  });

  it("renders the plain all-clear when nothing is open", () => {
    renderSurface({ atsReport: ats({}, [{ id: "contact-0", status: "pass" }]), gapClusters: [] });
    expect(screen.getByTestId("review-verdict").textContent).toBe("Nothing needs you any more – the document is ready to send.");
    expect(screen.queryByTestId("review-verdict-sub")).toBeNull();
  });
});

/* ------------------------------------------------------------------ cl. 6 */

/**
 * ADR-081 clause 6 / SF-REVIEW.2 — the visibility invariant, on the ONE layout.
 * The mutation it kills: rendering a group's count only when it is opened.
 */
describe("ADR-099 — the passed line (groups 2 and 4 are gone)", () => {
  it("renders no group toggles", () => {
    renderSurface();
    for (const id of [2, 4]) expect(screen.queryByTestId(`review-group-toggle-${id}`)).toBeNull();
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
  // ADR-099 cl. 2 / T-U: a producer that did not run is ONE counted "Nicht
  // geprüft" row with "Jetzt prüfen" — never zero, never the all-clear.
  it("Oracle absent: one counted unknown row with the re-check action", () => {
    renderSurface({ truthReport: null });
    const box = screen.getAllByTestId("review-unknown").find((b) => /Truthfulness Oracle/.test(b.textContent ?? ""))!;
    expect(box).toBeTruthy();
    expect(box.dataset.resolver).toBe("recheck");
    expect(within(box).getByTestId("review-unknown-recheck").textContent).toBe("Check now");
    expect(screen.getByTestId("review-verdict").textContent).not.toMatch(/ready to send/);
  });

  it("both group-1 producers absent: two unknown rows counted, never an all-clear or a zero", () => {
    // ADV-11: the critic ran clean here, so only the two group-1 producers are unknown.
    renderSurface({ atsReport: null, truthReport: null, criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 } as never, gapClusters: null });
    expect(screen.getAllByTestId("review-unknown")).toHaveLength(2);
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe("2");
    expect(screen.getByTestId("review-verdict").textContent).toBe("2 points need you before you send.");
  });

  it("keeps an unknown group OUT of the passed-checks collapse", () => {
    renderSurface({ atsReport: null, criticReport: null });
    expect(screen.queryByTestId("review-passed-checks")).toBeNull();
  });

  it("a producer that ran and found nothing renders no row, not unknown", () => {
    renderSurface({ atsReport: ats({}, []), gapClusters: [] });
    expect(screen.queryByTestId("review-group3-link")).toBeNull();
    expect(screen.queryByTestId("review-unknown")).toBeNull();
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe("0");
  });

  it("declares a producer that does not APPLY (the letter has no gap clusters) rather than faking one", () => {
    renderSurface({ documentKind: "cover-letter", gapClusters: [], hasClusterProducer: false });
    expect(screen.queryByTestId("review-group-unknown-3")).toBeNull();
  });
});

/* ------------------------------------------------------------------ cl. 2 */

describe("ADR-081 cl. 2 — one row citing both producers, and nothing else suppressed", () => {
  it("renders a genuine overlap as ONE card, counted once", () => {
    renderSurface({
      atsReport: ats({ present_unsupported: ["Lean-Management"] }),
      truthReport: truth([
        {
          claim: { text: "Lean–Management", location: "work-0", kind: "skill" },
          verdict: { verdict: "unbacked", checker: "literal", evidence: [], detail: null },
        },
      ]),
    });
    expect(screen.getAllByTestId("review-card")).toHaveLength(1);
    expect(screen.queryByTestId("review-group1-list")).toBeNull();
    expect(verdictNumber()).toBe(1);
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

/* ------------------------------------------------------ cl. 2 — locate */

describe("ADR-090 cl. 2 — Show me where", () => {
  it("names the document's own wording and the number of places", () => {
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(previewDoc) });
    expect(screen.getByTestId("review-card-matched").textContent).toBe(
      "Your document says “AI governance” in 2 places. Nothing in your profile backs it.",
    );
  });

  it("#797: several wordings use the catalog's quote convention in both locales", () => {
    const sapDoc = (): Document => {
      const doc = document.implementation.createHTMLDocument("cv");
      const p = doc.createElement("p");
      p.textContent = "Tägliche Arbeit mit SAP PP und SAP MM.";
      doc.body.append(p);
      return doc;
    };
    const sapAts = ats({
      present_unsupported: ["PP/MM"],
      present_unsupported_matches: { "PP/MM": [{ form: "SAP PP", stem: false }, { form: "SAP MM", stem: false }] },
    });
    render(withIntl(<ReviewSurface {...FULL_INPUTS} atsReport={sapAts} locator={makePreviewLocator(sapDoc)} />, "de"));
    expect(screen.getByTestId("review-card-matched").textContent).toBe(
      "Dein Dokument sagt „SAP PP“, „SAP MM“ an 2 Stellen. Nichts in deinem Profil belegt das.",
    );
    cleanup();
    render(withIntl(<ReviewSurface {...FULL_INPUTS} atsReport={sapAts} locator={makePreviewLocator(sapDoc)} />, "en"));
    expect(screen.getByTestId("review-card-matched").textContent).toBe(
      "Your document says “SAP PP”, “SAP MM” in 2 places. Nothing in your profile backs it.",
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
    // Ruling C-1 (2): no editor handle on the card.
    expect(screen.queryByTestId("review-action-edit")).toBeNull();
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
    // The card's answers ride along in the locate sheet (ADR-099: one card, its answers).
    expect(within(view).getByTestId("review-action-takeout")).toBeTruthy();
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
          if (refreshedReport(r) !== undefined) setReport(refreshedReport(r)!);
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
      // Contract 2 as WP-A built it: the kind's ATS-report RESPONSE, not the bare report.
      report: { document_id: "generated-cv-1", status: "ready", report: AFTER, review_state: TAKEN_STATE },
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

    // Next finding → the next open one.
    fireEvent.click(screen.getByTestId("review-action-next"));
    expect(screen.getByTestId("review-card").getAttribute("data-finding-key")).toBe("ats:collibra");
  });

  it("still listed after the rewrite → the card says so and offers the editor (ADR-090 cl. 5)", async () => {
    api.takeOut.mockResolvedValue({
      changes: [{ section_id: "summary", before: "a AI governance b", after: "a AI-Governance b" }],
      still_listed: true,
      report: MATCHED_ATS,
      truthfulness: null,
      review_state: TAKEN_STATE,
    });
    render(withIntl(<Harness onEditFinding={vi.fn()} />));
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getByTestId("review-takeout-still")).toBeTruthy());
    expect(screen.getByTestId("review-takeout-still").textContent).toBe(
      "The check still finds this in the document. Edit it yourself to remove it.",
    );
    expect(screen.getByTestId("review-action-edit-after")).toBeTruthy();
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
            if (refreshedReport(r) !== undefined) setReport(refreshedReport(r)!);
            setState(r.review_state);
          }}
        />
      );
    }
    render(withIntl(<Harness />));
    fireEvent.click(screen.getByTestId("review-action-add"));
    const box = await screen.findByTestId("review-add-box");
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
    fireEvent.change(await screen.findByTestId("review-add-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("review-add-submit"));
    await waitFor(() => expect(screen.getByTestId("review-testimony-result")).toBeTruthy());
    expect(screen.getByTestId("review-testimony-result").getAttribute("data-status")).toBe("needs_confirmation");
    expect(screen.getByTestId("review-card").getAttribute("data-status")).toBe("open");
  });

  it("R2-13: a testimony that was not saved says so in the UI language, never the English detail", async () => {
    api.addEvidence.mockResolvedValue({
      testimony: { status: "error", error_code: "not_saved", detail: "ReconcileFailedError: provider reply unparsable", changes: [] },
      report: MATCHED_ATS,
      truthfulness: null,
      review_state: null,
    });
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-action-add"));
    fireEvent.change(await screen.findByTestId("review-add-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("review-add-submit"));
    await waitFor(() => expect(screen.getByTestId("review-testimony-result")).toBeTruthy());
    const text = screen.getByTestId("review-testimony-result").textContent ?? "";
    // Lead V owns `profile.testimony.statusNotSaved`; until it is merged the
    // generic error line stands (and then the detail is the only signal).
    const notSaved = (enMessages as { profile: { testimony: Record<string, string> } }).profile.testimony
      .statusNotSaved;
    if (notSaved) {
      expect(text).toBe(notSaved);
      expect(text).not.toContain("ReconcileFailedError");
    } else {
      expect(text).toContain("ReconcileFailedError");
    }
  });

  it("Cancel closes the box without a request", async () => {
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-action-add"));
    fireEvent.click(await screen.findByTestId("review-add-cancel"));
    expect(screen.queryByTestId("review-add-box")).toBeNull();
    expect(api.addEvidence).not.toHaveBeenCalled();
  });

  // ADR-099 cl. 6 retires #797's "supported" closure (CLOSES_LISTED is empty):
  // a wording the profile holds asks nothing, and the finding stays OPEN while
  // the live report lists it — "Stimmt" alone never makes a listed row go away.
  it("ADR-099 cl. 6: a wording the profile holds asks nothing, and the listed finding stays open", async () => {
    const decision = {
      finding_key: "ats:it data & ai governance", label: "IT Data & AI Governance", action: "supported",
      at: "2026-10-21T10:00:00Z", undo: null,
      receipt: [{ path: "work_experience[0].responsibilities[0]", text: "Leitete das AI governance board bei Nordwerk." }],
    };
    api.confirmSupported.mockResolvedValue({
      still_listed: true, report: MATCHED_ATS, truthfulness: null,
      review_state: { walked_at: null, decisions: [decision] },
    });
    function Harness() {
      const [state, setState] = React.useState<ReviewState | null>(null);
      return (
        <ReviewSurface {...FULL_INPUTS} atsReport={MATCHED_ATS} reviewState={state}
          onRefresh={(r) => setState(r.review_state)} />
      );
    }
    render(withIntl(<Harness />));
    fireEvent.click(screen.getByTestId("review-action-add"));
    await waitFor(() => expect(api.confirmSupported).toHaveBeenCalledWith("cv", "generated-cv-1", "ats:it data & ai governance"));
    expect(screen.queryByTestId("review-add-box")).toBeNull();
    expect(api.addEvidence).not.toHaveBeenCalled();
    expect(screen.queryByTestId("review-row-status-supported")).toBeNull();
    expect(screen.getByTestId("review-card").getAttribute("data-status")).toBe("open");
    expect(verdictNumber()).toBe(3);
  });

  it("#797: a wording the profile lacks opens the ask (the server answered not_in_profile)", async () => {
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-action-add"));
    expect(await screen.findByTestId("review-add-box")).toBeTruthy();
    expect(api.confirmSupported).toHaveBeenCalledTimes(1);
  });
});

/* ------------------------------------------------------- cl. 5 — edit */

describe("ADR-090 cl. 5 — the editor, after a take-out that left the finding listed", () => {
  it("hands the page the finding, its targets and the place being shown", async () => {
    api.takeOut.mockResolvedValue({
      changes: [{ section_id: "summary", before: "a AI governance b", after: "a AI-Governance b" }],
      still_listed: true, report: MATCHED_ATS, truthfulness: null, review_state: null,
    });
    const onEditFinding = vi.fn();
    const doc = previewDoc();
    renderSurface({ atsReport: MATCHED_ATS, locator: makePreviewLocator(() => doc), onEditFinding });
    fireEvent.click(screen.getByTestId("review-action-locate"));
    fireEvent.click(screen.getByTestId("review-locate-next"));
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    fireEvent.click(await screen.findByTestId("review-action-edit-after"));
    expect(onEditFinding).toHaveBeenCalledWith(
      expect.objectContaining({
        findingKey: "ats:it data & ai governance",
        label: "IT Data & AI Governance",
        targets: [{ form: "AI governance", stem: false }],
      }),
    );
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


/* ------------------------------------------------ ruling B-1 — stem-only */

describe("ruling B-1 — a stem-only finding offers no Take it out", () => {
  function atsWith(matches: Array<{ form: string; stem: boolean }>): ATSReport {
    return ats({ present_unsupported: ["Mentoring"], present_unsupported_matches: { Mentoring: matches } });
  }

  it("every matched form stem-only → no Take it out; Show me where and Stimmt stay", () => {
    renderSurface({ atsReport: atsWith([{ form: "Mentoring", stem: true }]), locator: makePreviewLocator(previewDoc) });
    const card = screen.getByTestId("review-card");
    expect(within(card).queryByTestId("review-action-takeout")).toBeNull();
    expect(within(card).getByTestId("review-action-add")).toBeTruthy();
    expect(within(card).queryByTestId("review-action-edit")).toBeNull();
  });

  it("mixed stem and non-stem forms → Take it out stays", () => {
    renderSurface({
      atsReport: atsWith([
        { form: "Mentoring", stem: true },
        { form: "Mentor", stem: false },
      ]),
    });
    expect(screen.getByTestId("review-action-takeout")).toBeTruthy();
  });

  it("a 409 refusal lands on the existing still-listed path", async () => {
    const { ReviewActionError } = await import("@/lib/api/document-review");
    api.takeOut.mockRejectedValue(new ReviewActionError(409));
    renderSurface({ atsReport: atsWith([{ form: "Mentor", stem: false }]), onEditFinding: vi.fn() });
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getByTestId("review-takeout-still")).toBeTruthy());
    expect(screen.queryByTestId("review-action-error")).toBeNull();
    expect(screen.getByTestId("review-action-edit-after")).toBeTruthy();
  });
});

/* ----------------------------------------- D-3 — the changed passages only */

describe("D-3 — This is what changed shows the changed passage, not the section", () => {
  it("strikes only the changed sentence of a long body and elides the rest", async () => {
    const before =
      "Dear team,\n\nI built European e-commerce platforms for ten years. I also ran the payment stack.\n\nHappy to talk.";
    const after = "Dear team,\n\nI built platforms for ten years. I also ran the payment stack.\n\nHappy to talk.";
    api.takeOut.mockResolvedValue({
      changes: [{ section_id: "body", before, after }],
      still_listed: false,
      report: ats({ present_unsupported: [] }),
      truthfulness: null,
      review_state: null,
    });
    renderSurface({ atsReport: MATCHED_ATS });
    fireEvent.click(screen.getByTestId("review-action-takeout"));
    await waitFor(() => expect(screen.getAllByTestId("review-takeout-before")).toHaveLength(1));
    expect(screen.getByTestId("review-takeout-before").textContent).toBe(
      "I built European e-commerce platforms for ten years.",
    );
    expect(screen.getByTestId("review-takeout-after").textContent).toBe("I built platforms for ten years.");
    const change = screen.getByTestId("review-takeout-change");
    expect(change.textContent).not.toContain("Dear team");
    expect(change.textContent).not.toContain("payment stack");
    expect(change.textContent).toContain("…");
  });
});

/* ------------------------------------------------------------------ #759 */

describe("#759 under ADR-099 — a failed document-language check is never the all-clear", () => {
  const MIXED = {
    id: "document-language",
    status: "fail" as const,
    details: "items not written in the document language (de): [Nivalo GmbH] 'Campaign development'",
    details_key: "document-language-mixed",
    details_params: { language: "de", count: 2, examples: "Campaign development, including end-to-end" },
  };

  // ADR-099 cl. 9: a classified mixed-language residue is the "Diese Stellen
  // übersetzen" card (class A, resolver translate).
  const CLASSIFIED = { ...MIXED, delivery_class: "A" as const, resolver: "translate" };

  it("classified: the translate card, counted, with the one action", () => {
    renderSurface({ atsReport: { ...ats({}, [CLASSIFIED]), delivery: { loop_clean: true, removals: [], fixed: [] } } as ATSReport, gapClusters: [] });
    const box = screen.getByTestId("review-translate");
    expect(within(box).getByTestId("review-translate-action").textContent).toBe("Translate these passages");
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe("1");
    expect(screen.getByTestId("review-verdict").textContent).not.toMatch(/ready to send/);
  });

  it("unclassified (a pre-ADR-099 report): the counted fallback row, label only — no English details", () => {
    render(withIntl(<ReviewSurface {...FULL_INPUTS} atsReport={ats({}, [MIXED])} gapClusters={[]} />, "de"));
    const row = screen.getByTestId("review-fallback-document-language");
    expect(row.dataset.counted).toBe("true");
    expect(screen.getByTestId("review-surface").textContent ?? "").not.toMatch(/items not written/);
    expect(screen.getByTestId("review-verdict").textContent).toBe("1 Punkt braucht Dich, bevor Du sendest.");
  });

  it("no row when the check passed", () => {
    renderSurface({ atsReport: ats({}, [{ id: "document-language", status: "pass" }]), gapClusters: [] });
    expect(screen.queryByTestId("review-translate")).toBeNull();
    expect(screen.queryByTestId("review-fallback-document-language")).toBeNull();
  });
});

/* ------------------------------------------------- #786 / T-2 degradation */

describe("ADR-081 cl. 9 amended (#786, T-2) — a review step that did not run is told above the verdict", () => {
  it("renders the degradation note before the verdict and wires the regenerate path", () => {
    const onRegenerate = vi.fn();
    renderSurface({
      degradation: [{ check: "cv_language", stage: "review", reason: "timeout", attempt: 1, count: 3 }],
      onRegenerate,
    });
    const note = screen.getByTestId("review-degradation");
    const verdict = screen.getByTestId("review-verdict");
    expect(note.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(note).toHaveTextContent("Language check: did not run — the AI provider did not answer in time.");
    fireEvent.click(screen.getByTestId("review-degradation-regenerate"));
    expect(onRegenerate).toHaveBeenCalledOnce();
  });

  it("renders no note for an unrecorded or clean record", () => {
    renderSurface({ degradation: [] });
    expect(screen.queryByTestId("review-degradation")).toBeNull();
  });
});
