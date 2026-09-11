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

/**
 * #667 — the section editor is a FOURTH handle (ADR-081 cl. 3, amended
 * 2026-09-11), and #671 — group 4 renders the terminal review's observations
 * (ADR-076 cl. 9).
 *
 * The `terminal-review` payloads below are VERBATIM from the Nougat build-1
 * delivery run (`35-cv-ats-report.json` / `36-cl-ats-report.json`, the
 * synthetic `operations_marcus_de` case), truncated only in length. Using the
 * producer's real output rather than a hand-written stub is the point: #671's
 * premise is a claim about what the surface does with the REAL shape.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSCheck, ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

/** Verbatim from 35-cv-ats-report.json (delivery run 2026-09-10), shortened. */
const TERMINAL_REVIEW_FAIL: ATSCheck = {
  id: "terminal-review",
  driver: null,
  status: "fail",
  details:
    "The terminal review exhausted its retries with findings still open, and the " +
    "document was delivered unreviewed after 2 round(s). Open findings: Work history " +
    "index 0, Weberit Kunststofftechnik GmbH: the bullet states that Lean Production " +
    "was implemented through 5S activities, but 5S is not explicitly documented as a " +
    "Weberit responsibility or achievement.",
  details_key: null,
  details_params: null,
};

/** The minor-only settle: ADR-039 has no fourth band, so this is a PASS that
 *  still names its observations (founder ruling 7, 2026-09-05). */
const TERMINAL_REVIEW_MINOR_ONLY: ATSCheck = {
  id: "terminal-review",
  driver: null,
  status: "pass",
  details:
    "The terminal review settled on minor findings only, and the document was " +
    "delivered. Observations: the letter repeats the employer's name in most " +
    "paragraphs; several sentences open with the same construction.",
  details_key: null,
  details_params: null,
};

/** #671's second fact: `driver` carries a SECOND key since narrative-evidence. */
const NARRATIVE_EVIDENCE: ATSCheck = {
  id: "narrative-evidence",
  driver: { concepts: 8 },
  status: "fail",
  details: "8 JD-required concepts reach the document only as a skills tag.",
  details_key: null,
  details_params: null,
};

const PAGE_LENGTH_PIN_DRIVER: ATSCheck = {
  id: "page-length",
  driver: { pinned_facts: 3 },
  status: "fail",
  details: "3 pages against a 2-page target.",
  details_key: null,
  details_params: null,
};

function ats(checks: ATSCheck[], keywords: Partial<NonNullable<ATSReport>["keywords"]> = {}): ATSReport {
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

const TRUTH: TruthfulnessReport = {
  version: "1",
  document_kind: "cv",
  claims: [],
  counts: {},
  stated_limit: "",
};

const CRITIC_RAN: OutcomeCriticReport = { ran: true, mount: "cv", advisories: [], dropped_citations: 0 };

function base(overrides: Partial<ReviewSurfaceProps> = {}): ReviewSurfaceProps {
  return {
    documentKind: "cv",
    documentId: "generated-cv-1",
    atsReport: ats([]),
    truthReport: TRUTH,
    criticReport: CRITIC_RAN,
    gapClusters: [],
    modePreference: "overview",
    ...overrides,
  };
}

/** Idempotent: ADR-081 cl. 5 opens the FIRST present group automatically, so a
 *  blind click would close the very group under test. */
function open(group: number) {
  const toggle = screen.getByTestId(`review-group-toggle-${group}`);
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

describe("#671 — group 4 renders the terminal review's details", () => {
  it("renders the FAIL details verbatim on the CV surface", () => {
    render(withIntl(<ReviewSurface {...base({ atsReport: ats([TERMINAL_REVIEW_FAIL]) })} />));
    open(4);
    const group = screen.getByTestId("review-group-4");
    // The label comes from `ats.checks.terminal-review`, not from the raw id.
    expect(within(group).getByText("Final document review")).toBeTruthy();
    expect(
      within(group).getByText(/exhausted its retries with findings still open/),
    ).toBeTruthy();
    // The open findings themselves, not only the status sentence — a status
    // without its finding tells the user something is wrong and not what.
    expect(within(group).getByText(/Weberit Kunststofftechnik GmbH/)).toBeTruthy();
  });

  it("renders the FAIL details on the cover-letter surface too", () => {
    render(
      withIntl(
        <ReviewSurface
          {...base({
            documentKind: "cover_letter",
            documentId: "generated-cl-1",
            atsReport: ats([TERMINAL_REVIEW_FAIL]),
            gapClusters: [],
            hasClusterProducer: false,
          })}
        />,
      ),
    );
    open(4);
    expect(
      within(screen.getByTestId("review-group-4")).getByText(
        /exhausted its retries with findings still open/,
      ),
    ).toBeTruthy();
  });

  it("a minor-only settle is a PASS that still names its observations", () => {
    render(
      withIntl(<ReviewSurface {...base({ atsReport: ats([TERMINAL_REVIEW_MINOR_ONLY]) })} />),
    );
    open(4);
    const group = screen.getByTestId("review-group-4");
    expect(within(group).getByText(/repeats the employer's name/)).toBeTruthy();
    // ADR-039 has no fourth band (founder ruling 7): the row must not carry the
    // severity dot the FAIL rows use, and the group must not report a failure.
    const row = within(group).getByTestId("review-item-g4-check-advisory-terminal-review");
    expect(row.querySelector(".bg-critical")).toBeNull();
    expect(within(group).queryByTestId("review-item-g4-check-fail-terminal-review")).toBeNull();
  });

  it("a pass WITHOUT details stays collapsed into the passed-checks line", () => {
    const clean: ATSCheck = { id: "terminal-review", status: "pass", details: null, driver: null };
    render(withIntl(<ReviewSurface {...base({ atsReport: ats([clean]) })} />));
    expect(screen.getByTestId("review-passed-checks").textContent).toContain("1");
    expect(screen.queryByTestId("review-item-g4-check-advisory-terminal-review")).toBeNull();
  });

  it("reads ATSCheck.driver as a MAP — a second key does not hide the check", () => {
    // Frontend collector #677 / #671: `driver` was `{pinned_facts}` only until
    // `narrative-evidence` added `{concepts}`. A consumer that destructures one
    // fixed key must not swallow the other check.
    render(
      withIntl(
        <ReviewSurface
          {...base({ atsReport: ats([NARRATIVE_EVIDENCE, PAGE_LENGTH_PIN_DRIVER]) })}
        />,
      ),
    );
    open(4);
    const group = screen.getByTestId("review-group-4");
    expect(within(group).getByText(/8 JD-required concepts/)).toBeTruthy();
    expect(within(group).getByText(/3 pages against a 2-page target/)).toBeTruthy();
  });
});

describe("#667 — the section editor is a fourth handle", () => {
  const CLAIMABLE_CLUSTER = { id: "gap-7", label: "SAP PP", kind: "claimable" as const };

  it("group 2 names FOUR handles, the fourth being the section editor", () => {
    render(
      withIntl(
        <ReviewSurface
          {...base({
            atsReport: ats([], { missing_claimable: ["SAP PP"] }),
            onEditGapSection: vi.fn(),
          })}
        />,
      ),
    );
    open(2);
    expect(screen.getByTestId("review-group2-handle-editor")).toBeTruthy();
    expect(screen.getByTestId("review-group2-handle-pin")).toBeTruthy();
    expect(screen.getByTestId("review-group2-handle-pages")).toBeTruthy();
    expect(screen.getByTestId("review-group2-handle-regenerate")).toBeTruthy();
  });

  it("a claimable gap cluster in group 2 is a button that hands the gap over", () => {
    const onEditGapSection = vi.fn();
    render(
      withIntl(
        <ReviewSurface {...base({ gapClusters: [CLAIMABLE_CLUSTER], onEditGapSection })} />,
      ),
    );
    open(2);
    const row = screen.getByTestId("review-item-g2-mc-cluster-gap-7");
    expect(row.tagName).toBe("BUTTON");
    expect(row.getAttribute("data-handle")).toBe("section-editor");
    fireEvent.click(row);
    expect(onEditGapSection).toHaveBeenCalledWith("gap-7");
  });

  it("without the handler the row is still SHOWN but inert — a surface with no editor", () => {
    // The cover-letter page supplies no `onEditGapSection` (it has no section
    // editor). The finding must still be visible — hiding it would trade an
    // inert row for a missing one, which is the SF-REVIEW.1 failure mode.
    render(
      withIntl(
        <ReviewSurface
          {...base({
            documentKind: "cover_letter",
            gapClusters: [CLAIMABLE_CLUSTER],
            hasClusterProducer: false,
          })}
        />,
      ),
    );
    open(2);
    const row = screen.getByTestId("review-item-g2-mc-cluster-gap-7");
    expect(row.tagName).toBe("LI");
    expect(row.getAttribute("data-handle")).toBeNull();
  });

  it("a group-2 TERM row is still not clickable — there is no section to preselect", () => {
    render(
      withIntl(
        <ReviewSurface
          {...base({
            atsReport: ats([], { missing_claimable: ["SAP PP"] }),
            onEditGapSection: vi.fn(),
          })}
        />,
      ),
    );
    open(2);
    const rows = screen
      .getByTestId("review-group-2")
      .querySelectorAll('[data-testid^="review-item-g2-mc-term-"]');
    expect(rows.length).toBe(1);
    expect(rows[0].tagName).toBe("LI");
  });
});
