// Copyright (C) 2024-2026 Tobias Rosenbaum
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

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import ATSChecksPanel, { type ATSReport } from "../ATSChecksPanel";

const REPORT_WITH_FAILURES: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "contact-phone", status: "fail", details: "No phone found" },
    { id: "work-1", status: "pass" },
    { id: "reading-order", status: "fail", details: null },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript", "React"],
    missing: ["Kubernetes", "Docker"],
  },
};

// US203: the report splits missing keywords into claimable (held but absent — fixable)
// vs honest-gap (not in the profile). The two buckets must render distinctly.
const REPORT_WITH_BUCKETS: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript"],
    missing: ["React", "Kubernetes"],
    missing_claimable: ["React"],
    missing_honest_gap: ["Kubernetes"],
  },
};

const REPORT_WITH_UNSUPPORTED: ATSReport = {
  checks: [{ id: "contact-name", status: "pass" }],
  keywords: {
    present: ["TypeScript", "DevSecOps"],
    missing: [],
    missing_claimable: [],
    missing_honest_gap: [],
    present_unsupported: ["DevSecOps"],
  },
};

// E042/US239 (ADR-051): a page-length check can PASS with a non-null `details`
// string (advisory states — "meets your chosen target" / "acceptable for senior
// profiles"). The trap: the panel only rendered `details` for FAILING checks —
// this report exercises the advisory-pass path, mirroring the real backend shape.
// E042 follow-up (ADR-038): the backend now also sends details_key/details_params
// so the panel can localise the advisory; `details` stays the EN fallback.
const REPORT_WITH_ADVISORY: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    {
      id: "page-length",
      status: "pass",
      details: "3 pages — meets your chosen target of 3; the DACH norm is 2 pages",
      details_key: "page-length-target",
      details_params: { pages: 3, target: 3, region: "DACH", standard: 2 },
    },
  ],
  keywords: {
    present: ["TypeScript"],
    missing: [],
  },
};

// A legacy persisted report (pre-details_key JSONB) — the panel must fall back to
// the raw EN `details` string instead of rendering nothing or a raw key path.
const REPORT_LEGACY_ADVISORY: ATSReport = {
  checks: [
    {
      id: "page-length",
      status: "pass",
      details: "3 pages — acceptable for senior profiles; the DACH norm is 2 pages",
    },
  ],
  keywords: { present: [], missing: [] },
};

const REPORT_ALL_PASS: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "work-0", status: "pass" },
    { id: "work-1", status: "pass" },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript", "React", "Node.js"],
    missing: [],
  },
};

describe("ATSChecksPanel", () => {
  // Case 1: failed checks render inline on the compact card — visible without any interaction
  it("renders failed checks inline with data-testid ats-check-<id> and details", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const phoneCheck = screen.getByTestId("ats-check-contact-phone");
    expect(phoneCheck).toBeInTheDocument();
    expect(phoneCheck.textContent).toContain("No phone found");

    // Failed check without details must still render
    expect(screen.getByTestId("ats-check-reading-order")).toBeInTheDocument();

    // Only the FAILED checks render inline — passing checks live in the drawer
    expect(screen.getAllByTestId(/^ats-check-/)).toHaveLength(2);
  });

  // Case 2: all-pass report renders the compact happy path — no inline check rows
  it("renders no inline check rows when every check passes", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryAllByTestId(/^ats-check-/)).toHaveLength(0);
    expect(screen.getByTestId("ats-structure-status")).toBeInTheDocument();
  });

  // Case 3: missing keywords listed on the card
  it("lists missing keywords by name in ats-keywords-missing", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const missingEl = screen.getByTestId("ats-keywords-missing");
    expect(missingEl.textContent).toContain("Kubernetes");
    expect(missingEl.textContent).toContain("Docker");
  });

  // Case 4: keyword coverage ring shows X/Y counts — never a percentage
  it("renders keyword coverage as X of Y counts in ats-keywords-coverage", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const coverageEl = screen.getByTestId("ats-keywords-coverage");
    // present=2, total=4
    expect(coverageEl.textContent).toMatch(/2/);
    expect(coverageEl.textContent).toMatch(/4/);
    expect(coverageEl.textContent).not.toContain("%");
  });

  // Case 5: report === null → neutral unavailable state, no crash
  it("renders ats-unavailable when report is null", () => {
    render(withIntl(<ATSChecksPanel report={null} />));
    expect(screen.getByTestId("ats-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("ats-panel")).toBeNull();
  });

  // Case 6: no aggregate score — no "%" character anywhere, even with the drawer open
  it("renders no percentage/aggregate score anywhere", () => {
    const { container } = render(
      withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />)
    );
    fireEvent.click(screen.getByTestId("ats-details-button"));
    expect(container.textContent).not.toContain("%");
  });

  // Case 7: details button opens the drawer with the full grouped checks list
  it("opens the drawer with grouped checks via the details button", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryByTestId("ats-drawer")).toBeNull();

    fireEvent.click(screen.getByTestId("ats-details-button"));
    expect(screen.getByTestId("ats-drawer")).toBeInTheDocument();

    // work-0 + work-1 collapse into ONE grouped row with a (2 of 2) count
    const workGroup = screen.getByTestId("ats-drawer-check-work");
    expect(workGroup.textContent).toMatch(/2/);
    // Single-instance checks render one row each
    expect(screen.getByTestId("ats-drawer-check-contact-name")).toBeInTheDocument();
    expect(screen.getByTestId("ats-drawer-check-skills")).toBeInTheDocument();
    // Keyword section is present in the drawer
    expect(screen.getByTestId("ats-drawer-coverage")).toBeInTheDocument();
  });

  // Case 8: drawer closes via the close button
  it("closes the drawer via the close button", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    fireEvent.click(screen.getByTestId("ats-details-button"));
    expect(screen.getByTestId("ats-drawer")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("ats-drawer-close"));
    expect(screen.queryByTestId("ats-drawer")).toBeNull();
  });

  // Case 9: failed checks surface in the drawer's grouped rows with details
  it("shows failure details inside the drawer groups", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    fireEvent.click(screen.getByTestId("ats-details-button"));
    const phoneGroup = screen.getByTestId("ats-drawer-check-contact-phone");
    expect(phoneGroup.textContent).toContain("No phone found");
  });

  // Bonus: all-pass report shows no missing keywords line
  it("does not render ats-keywords-missing when there are no missing keywords", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryByTestId("ats-keywords-missing")).toBeNull();
  });

  // US203: claimable vs honest-gap missing keywords render in two distinct buckets
  it("renders missing-claimable and missing-honest-gap as distinct buckets", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_BUCKETS} />));
    const claimable = screen.getByTestId("ats-keywords-missing-claimable");
    const honestGap = screen.getByTestId("ats-keywords-missing-honest-gap");
    expect(claimable.textContent).toContain("React");
    expect(claimable.textContent).not.toContain("Kubernetes");
    expect(honestGap.textContent).toContain("Kubernetes");
    expect(honestGap.textContent).not.toContain("React");
  });

  // US203: a bucket with no entries is not rendered
  it("omits a missing bucket that has no keywords", () => {
    const onlyHonest: ATSReport = {
      checks: [{ id: "skills", status: "pass" }],
      keywords: {
        present: [],
        missing: ["Kubernetes"],
        missing_claimable: [],
        missing_honest_gap: ["Kubernetes"],
      },
    };
    render(withIntl(<ATSChecksPanel report={onlyHonest} />));
    expect(screen.queryByTestId("ats-keywords-missing-claimable")).toBeNull();
    expect(screen.getByTestId("ats-keywords-missing-honest-gap")).toBeInTheDocument();
  });

  // US203: back-compat — a report with no bucket fields still renders without crashing
  it("renders legacy reports without missing buckets", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    expect(screen.getByTestId("ats-keywords-missing")).toBeInTheDocument();
  });

  // #117 (ADR-048 fourth quadrant): a present keyword without profile backing is a
  // truthfulness warning, not silent ordinary coverage.
  it("renders present-but-unsupported keywords as a warning row", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_UNSUPPORTED} />));
    const row = screen.getByTestId("ats-keywords-present-unsupported");
    expect(row).toBeInTheDocument();
    expect(row.textContent).toContain("DevSecOps");
  });

  it("omits the unsupported row when empty or absent", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_BUCKETS} />));
    expect(screen.queryByTestId("ats-keywords-present-unsupported")).toBeNull();
  });

  // E042/US239: a passing check WITH details (page-length advisory) must surface
  // its details on the compact card, distinctly from a failure.
  it("renders a passing check's details as an advisory row on the compact card", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow).toBeInTheDocument();
    expect(advisoryRow.textContent).toContain("meets your chosen target of 3");
    // Never rendered as a failure row
    expect(screen.queryByTestId("ats-check-page-length")).toBeNull();
  });

  // A pass-with-advisory check must not flip the overall structure status to "issues"
  it("keeps structureOk when the only details-bearing check is a pass", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    expect(screen.getByTestId("ats-structure-status")).toBeInTheDocument();
    expect(screen.queryByText(/structureIssues/)).toBeNull();
  });

  // The drawer's grouped view must also surface the advisory detail, styled
  // distinctly (data-testid) from a failing check's detail.
  it("shows a pass-with-advisory detail inside the drawer group", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    fireEvent.click(screen.getByTestId("ats-details-button"));
    const advisory = screen.getByTestId("ats-drawer-advisory-page-length");
    expect(advisory.textContent).toContain("meets your chosen target of 3");
    // The group itself still reads as passing (no failing entries)
    const group = screen.getByTestId("ats-drawer-check-page-length");
    expect(group.textContent).not.toContain("✗");
  });

  // E042 follow-up (ADR-038): a details_key must render the LOCALISED string in
  // German chrome — the raw backend-English `details` was the bug.
  it("localises a keyed advisory detail in the de locale", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow.textContent).toContain(
      "3 Seiten — entspricht Ihrem gewählten Ziel von 3; die DACH-Norm sind 2 Seiten",
    );
    expect(advisoryRow.textContent).not.toContain("meets your chosen target");
  });

  // Back-compat: a legacy report without details_key still shows its EN details.
  it("falls back to the raw details string when no details_key is present", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_LEGACY_ADVISORY} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow.textContent).toContain("acceptable for senior profiles");
  });

  // Adversarial find (2026-07-16): a KNOWN key with null/absent details_params must
  // fall back to the EN details — not render the raw key path with an IntlError.
  it("falls back to raw details when details_params is null for a known key", () => {
    const report: ATSReport = {
      checks: [
        {
          id: "page-length",
          status: "pass",
          details: "3 pages — meets your chosen target of 3; the DACH norm is 2 pages",
          details_key: "page-length-target",
          details_params: null,
        },
      ],
      keywords: { present: [], missing: [] },
    };
    render(withIntl(<ATSChecksPanel report={report} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow.textContent).toContain("meets your chosen target of 3");
    expect(advisoryRow.textContent).not.toContain("checkDetails");
  });
});
