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
import { render, screen } from "@testing-library/react";
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
    ...overrides,
  };
}

// ADR-099 supersedes what these suites pinned on groups 2 and 4: group 2 (the
// trade, its pin link and its fourth handle, #667 / 789:26) is gone, and the
// terminal review's open notes are class S (cl. 8) — never rendered, and never
// as server prose (cl. 12). What stays from #671 is that the REAL payloads are
// read without crashing and without hiding a failure: on a report persisted
// before ADR-099 (no delivery class) a failed check is the counted fallback
// row, its label only.

describe("R2-5 / #671 under ADR-099 — no server prose from the terminal review", () => {
  it("DE: an unclassified failed final check is one counted row, label only — no notes, no code, no rounds", () => {
    const keyed: ATSCheck = {
      ...TERMINAL_REVIEW_FAIL,
      details_key: "terminal-review-open",
      details_params: { count: 1 },
      notes: [
        { kind: "finding", text: TERMINAL_REVIEW_FAIL.details ?? "", plain: "Bei Weberit ist 5S nicht als Aufgabe belegt." },
        { kind: "code", key: "cv.role_budget", subjects: ["Weberit Kunststofftechnik GmbH"], text: "Over the per-role bullet budget" },
      ],
    };
    render(withIntl(<ReviewSurface {...base({ atsReport: ats([keyed]) })} />, "de"));
    const row = screen.getByTestId("review-fallback-terminal-review");
    expect(row.dataset.counted).toBe("true");
    expect(row.textContent).toContain("Schlussprüfung");
    const text = screen.getByTestId("review-surface").textContent ?? "";
    expect(text).not.toMatch(/round\(s\)|cv\.role_budget|exhausted its retries|Weberit/);
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe("1");
  });

  it("EN, both surfaces: the raw details never render", () => {
    for (const kind of ["cv", "cover-letter"] as const) {
      const { unmount } = render(
        withIntl(<ReviewSurface {...base({ documentKind: kind, atsReport: ats([TERMINAL_REVIEW_FAIL]), hasClusterProducer: kind === "cv" })} />),
      );
      expect(screen.getByTestId("review-fallback-terminal-review").textContent).toContain("Final check");
      expect(screen.getByTestId("review-surface").textContent ?? "").not.toMatch(/exhausted its retries|Weberit/);
      unmount();
    }
  });

  it("a classified terminal review with open notes (class S) renders nothing and counts nothing", () => {
    const s: ATSCheck = { ...TERMINAL_REVIEW_FAIL, delivery_class: "S", resolver: null };
    const report = { ...ats([s]), delivery: { loop_clean: true, removals: [], fixed: [] } } as ATSReport;
    render(withIntl(<ReviewSurface {...base({ atsReport: report })} />));
    expect(screen.queryByTestId("review-fallback-terminal-review")).toBeNull();
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe("0");
  });

  it("a minor-only settle is a PASS: counted in the passed line, its observations not shown", () => {
    render(withIntl(<ReviewSurface {...base({ atsReport: ats([TERMINAL_REVIEW_MINOR_ONLY]) })} />));
    expect(screen.getByTestId("review-passed-checks").textContent).toContain("1");
    expect(screen.getByTestId("review-surface").textContent ?? "").not.toMatch(/repeats the employer's name/);
    expect(screen.queryByTestId("review-fallback-terminal-review")).toBeNull();
  });

  it("a pass WITHOUT details stays collapsed into the passed-checks line", () => {
    const clean: ATSCheck = { id: "terminal-review", status: "pass", details: null, driver: null };
    render(withIntl(<ReviewSurface {...base({ atsReport: ats([clean]) })} />));
    expect(screen.getByTestId("review-passed-checks").textContent).toContain("1");
  });

  it("reads ATSCheck.driver as a MAP — a second key does not hide the failed check", () => {
    // #677 / #671: `driver` carries `{concepts}` besides `{pinned_facts}`.
    // Page length has ONE place — the page's card (cl. 7) — so its unclassified
    // fail is not a row; the narrative-evidence fail still is.
    render(withIntl(<ReviewSurface {...base({ atsReport: ats([NARRATIVE_EVIDENCE, PAGE_LENGTH_PIN_DRIVER]) })} />));
    expect(screen.getByTestId("review-fallback-narrative-evidence").dataset.counted).toBe("true");
    expect(screen.queryByTestId("review-fallback-page-length")).toBeNull();
    const text = screen.getByTestId("review-surface").textContent ?? "";
    expect(text).not.toMatch(/8 JD-required concepts|3 pages against/);
  });
});

describe("ADR-099 — group 2 and its handles are gone", () => {
  it("no group-2 toggle, no pin link, no gap-cluster handle buttons", () => {
    const onOpenPins = vi.fn();
    const onEditGapSection = vi.fn();
    render(
      withIntl(
        <ReviewSurface
          {...base({ atsReport: ats([], { missing_claimable: ["OEE"] }), onOpenPins, onEditGapSection })}
        />,
      ),
    );
    expect(screen.queryByTestId("review-group-toggle-2")).toBeNull();
    expect(screen.queryByTestId("review-group2-pins")).toBeNull();
    expect(screen.queryAllByRole("button").filter((b) => /OEE/.test(b.textContent ?? ""))).toHaveLength(0);
  });
});
