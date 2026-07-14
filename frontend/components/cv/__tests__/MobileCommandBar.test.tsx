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
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { MobileCommandBar } from "../MobileCommandBar";
import type { ATSReport } from "../ATSChecksPanel";

const REPORT: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "work-1", status: "fail" },
  ],
  keywords: { present: ["react"], missing: ["docker"] },
};

function renderBar(overrides: Partial<React.ComponentProps<typeof MobileCommandBar>> = {}) {
  const props = {
    atsReport: REPORT,
    atsPanel: <div data-testid="slot-ats">ats-panel</div>,
    fineTuneSurface: <div data-testid="slot-finetune">finetune-surface</div>,
    onDownloadPdf: vi.fn(),
    ...overrides,
  };
  render(withIntl(<MobileCommandBar {...props} />));
  return props;
}

describe("MobileCommandBar", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders the three command actions", () => {
    renderBar();
    expect(screen.getByTestId("command-ats")).toBeTruthy();
    expect(screen.getByTestId("command-finetune")).toBeTruthy();
    expect(screen.getByTestId("command-download")).toBeTruthy();
  });

  it("shows a pass-count badge reflecting passing checks", () => {
    renderBar();
    // 2 of the 3 checks pass
    expect(screen.getByTestId("command-ats-badge").textContent).toContain("2");
  });

  it("omits the badge when no ATS report is available", () => {
    renderBar({ atsReport: null });
    expect(screen.queryByTestId("command-ats-badge")).toBeNull();
  });

  it("wires the primary download action", () => {
    const { onDownloadPdf } = renderBar();
    fireEvent.click(screen.getByTestId("command-download"));
    expect(onDownloadPdf).toHaveBeenCalledOnce();
  });

  it("opens and closes the ATS bottom sheet", () => {
    renderBar();
    expect(screen.queryByTestId("slot-ats")).toBeNull();
    fireEvent.click(screen.getByTestId("command-ats"));
    expect(screen.getByTestId("command-sheet")).toBeTruthy();
    expect(screen.getByTestId("slot-ats")).toBeTruthy();
    fireEvent.click(screen.getByTestId("command-sheet-close"));
    expect(screen.queryByTestId("slot-ats")).toBeNull();
  });

  it("opens the Fine-tune sheet lazily with a read-degraded notice", () => {
    renderBar();
    // Not mounted before the sheet is opened
    expect(screen.queryByTestId("slot-finetune")).toBeNull();
    fireEvent.click(screen.getByTestId("command-finetune"));
    expect(screen.getByTestId("slot-finetune")).toBeTruthy();
    expect(screen.getByTestId("command-finetune-degraded")).toBeTruthy();
  });

  it("only one sheet is open at a time", () => {
    renderBar();
    fireEvent.click(screen.getByTestId("command-ats"));
    expect(screen.getByTestId("slot-ats")).toBeTruthy();
    fireEvent.click(screen.getByTestId("command-finetune"));
    expect(screen.queryByTestId("slot-ats")).toBeNull();
    expect(screen.getByTestId("slot-finetune")).toBeTruthy();
  });
});
