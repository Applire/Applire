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
});
