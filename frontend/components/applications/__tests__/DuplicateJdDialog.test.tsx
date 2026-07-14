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

// E039/US220 — journey Branch F: "This looks like the job you analyzed on
// <date>." Existing application offered first; never silently creates a ghost
// duplicate — and never blocks.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DuplicateJdDialog, type DuplicateOfHint } from "../DuplicateJdDialog";

// next-intl mock: expose interpolation params so body copy can be asserted
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${JSON.stringify(params)}` : key,
  useLocale: () => "de",
}));

const HINT: DuplicateOfHint = {
  application_id: "app-9",
  job_analysis_id: "job-9",
  company_name: "DataCraft GmbH",
  role_title: "Senior Data Analyst",
  analyzed_at: "2026-07-05T10:00:00Z",
  matched_on: "job",
};

describe("DuplicateJdDialog", () => {
  const onOpenExisting = vi.fn();
  const onContinueNew = vi.fn();
  const onDismiss = vi.fn();

  beforeEach(() => {
    onOpenExisting.mockReset();
    onContinueNew.mockReset();
    onDismiss.mockReset();
  });

  function renderDialog(hint: DuplicateOfHint = HINT) {
    return render(
      <DuplicateJdDialog
        hint={hint}
        onOpenExisting={onOpenExisting}
        onContinueNew={onContinueNew}
        onDismiss={onDismiss}
      />,
    );
  }

  it("names the existing application: company, role and analyzed date", () => {
    renderDialog();
    const body = screen.getByTestId("duplicate-jd-body");
    expect(body.textContent).toContain("DataCraft GmbH");
    expect(body.textContent).toContain("Senior Data Analyst");
    expect(body.textContent).toContain("5.7.2026");
  });

  it("falls back to a date-only body when company/role are unknown", () => {
    renderDialog({ ...HINT, company_name: null, role_title: null });
    const body = screen.getByTestId("duplicate-jd-body");
    expect(body.textContent).toContain("duplicateBodyPlain");
    expect(body.textContent).toContain("5.7.2026");
  });

  it("offers 'open existing' first and fires its callback", () => {
    renderDialog();
    const buttons = screen.getAllByRole("button");
    // journey Branch F default: the existing application is offered first
    expect(buttons[0]).toHaveAttribute("data-testid", "duplicate-jd-open-existing");
    fireEvent.click(screen.getByTestId("duplicate-jd-open-existing"));
    expect(onOpenExisting).toHaveBeenCalledTimes(1);
    expect(onContinueNew).not.toHaveBeenCalled();
  });

  it("'continue as new' fires its callback", () => {
    renderDialog();
    fireEvent.click(screen.getByTestId("duplicate-jd-continue-new"));
    expect(onContinueNew).toHaveBeenCalledTimes(1);
    expect(onOpenExisting).not.toHaveBeenCalled();
  });

  it("can be dismissed without picking either option (never blocks)", () => {
    renderDialog();
    fireEvent.click(screen.getByTestId("duplicate-jd-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onOpenExisting).not.toHaveBeenCalled();
    expect(onContinueNew).not.toHaveBeenCalled();
  });

  // US224 — same component, presentation-only divergence (ADR-050 §2): below
  // md this re-presents as a bottom sheet; md and up keeps the centred modal.
  describe("mobile presentation (US224)", () => {
    it("pins the panel to the bottom of the viewport below md, centred at md+", () => {
      renderDialog();
      const overlay = screen.getByTestId("duplicate-jd-dialog");
      expect(overlay.className).toContain("items-end");
      expect(overlay.className).toContain("md:items-center");
    });

    it("gives the panel bottom-sheet corners below md and a bounded modal card at md+", () => {
      renderDialog();
      const overlay = screen.getByTestId("duplicate-jd-dialog");
      const panel = overlay.firstElementChild as HTMLElement;
      expect(panel.className).toContain("rounded-t-2xl");
      expect(panel.className).toContain("md:rounded-xl");
      expect(panel.className).toContain("w-full");
      expect(panel.className).toContain("md:max-w-md");
    });

    it("still exposes every action (open existing, continue new, dismiss) in the mobile presentation", () => {
      renderDialog();
      expect(screen.getByTestId("duplicate-jd-open-existing")).toBeInTheDocument();
      expect(screen.getByTestId("duplicate-jd-continue-new")).toBeInTheDocument();
      expect(screen.getByTestId("duplicate-jd-dismiss")).toBeInTheDocument();
    });
  });
});
