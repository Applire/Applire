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

import { render, screen } from "@testing-library/react";
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

const REPORT_ALL_PASS: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript", "React", "Node.js"],
    missing: [],
  },
};

describe("ATSChecksPanel", () => {
  // Case 1: renders one row per check with pass/fail indication
  it("renders one row per check with data-testid ats-check-<id>", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    for (const check of REPORT_WITH_FAILURES.checks) {
      expect(screen.getByTestId(`ats-check-${check.id}`)).toBeInTheDocument();
    }
    expect(screen.getAllByTestId(/^ats-check-/)).toHaveLength(
      REPORT_WITH_FAILURES.checks.length
    );
  });

  // Case 2: failed checks render visibly and show details text when present
  it("renders failed checks visibly and shows details when present", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const phoneCheck = screen.getByTestId("ats-check-contact-phone");
    expect(phoneCheck).toBeInTheDocument();
    // Details text must appear for the phone check
    expect(phoneCheck.textContent).toContain("No phone found");

    // Failed check without details must still render
    const readingCheck = screen.getByTestId("ats-check-reading-order");
    expect(readingCheck).toBeInTheDocument();
  });

  // Case 3: missing keywords listed
  it("lists missing keywords by name in ats-keywords-missing", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const missingEl = screen.getByTestId("ats-keywords-missing");
    expect(missingEl.textContent).toContain("Kubernetes");
    expect(missingEl.textContent).toContain("Docker");
  });

  // Case 4: keyword coverage "X of Y" — counts, not a percentage
  it("renders keyword coverage as X of Y counts in ats-keywords-coverage", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const coverageEl = screen.getByTestId("ats-keywords-coverage");
    // present=2, total=4
    expect(coverageEl.textContent).toMatch(/2/);
    expect(coverageEl.textContent).toMatch(/4/);
    // Must not be a percentage
    expect(coverageEl.textContent).not.toContain("%");
  });

  // Case 5: report === null → neutral unavailable state, no crash
  it("renders ats-unavailable when report is null", () => {
    render(withIntl(<ATSChecksPanel report={null} />));
    expect(screen.getByTestId("ats-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("ats-panel")).toBeNull();
  });

  // Case 6: no aggregate score — no "%" character anywhere in output
  it("renders no percentage/aggregate score even with failures", () => {
    const { container } = render(
      withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />)
    );
    expect(container.textContent).not.toContain("%");
  });

  // Bonus: all-pass report shows no missing keywords section
  it("does not render ats-keywords-missing when there are no missing keywords", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryByTestId("ats-keywords-missing")).toBeNull();
  });

  // Bonus: coverage shows correctly for all-pass
  it("shows correct coverage counts for all-pass report", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    const coverageEl = screen.getByTestId("ats-keywords-coverage");
    // present=3, total=3
    expect(coverageEl.textContent).toMatch(/3/);
  });
});
