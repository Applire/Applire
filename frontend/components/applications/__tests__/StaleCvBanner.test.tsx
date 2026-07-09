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

// E039/US221 — journey Branch H: "Your profile grew since this CV was
// generated. Re-tailor?" Nudge with an explained delta; never auto-regenerates.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StaleCvBanner } from "../StaleCvBanner";
import type { StaleCVGained } from "@/lib/stale-cv";

// next-intl mock: expose interpolation params so body copy can be asserted
vi.mock("next-intl", () => ({
  useTranslations: (ns: string) => {
    const t = (key: string, params?: Record<string, unknown>) =>
      ns === "profile"
        ? ({ sectionSkills: "Fähigkeiten", sectionWorkExperience: "Berufserfahrung" }[key] ?? key)
        : params
          ? `${key}:${JSON.stringify(params)}`
          : key;
    return t;
  },
}));

const GAINED: StaleCVGained[] = [
  { section: "skills", count: 3 },
  { section: "work_experience", count: 1 },
];

describe("StaleCvBanner", () => {
  const onRetailor = vi.fn();
  const onDismiss = vi.fn();

  beforeEach(() => {
    onRetailor.mockReset();
    onDismiss.mockReset();
  });

  function renderBanner(props: Partial<Parameters<typeof StaleCvBanner>[0]> = {}) {
    return render(
      <StaleCvBanner
        gained={GAINED}
        canRetailor
        retailoring={false}
        onRetailor={onRetailor}
        onDismiss={onDismiss}
        {...props}
      />,
    );
  }

  it("explains WHAT the profile gained, with localized section labels", () => {
    renderBanner();
    const body = screen.getByTestId("stale-cv-body");
    expect(body.textContent).toContain("Fähigkeiten +3, Berufserfahrung +1");
  });

  it("falls back to a plain body when the delta is empty", () => {
    renderBanner({ gained: [] });
    expect(screen.getByTestId("stale-cv-body").textContent).toContain(
      "staleCvBodyPlain",
    );
  });

  it("offers re-tailor first and fires its callback", () => {
    renderBanner();
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveAttribute("data-testid", "stale-cv-retailor");
    fireEvent.click(screen.getByTestId("stale-cv-retailor"));
    expect(onRetailor).toHaveBeenCalledTimes(1);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("dismiss fires its callback and never re-tailors", () => {
    renderBanner();
    fireEvent.click(screen.getByTestId("stale-cv-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onRetailor).not.toHaveBeenCalled();
  });

  it("disables re-tailor while a generation is already running", () => {
    renderBanner({ retailoring: true });
    expect(screen.getByTestId("stale-cv-retailor")).toBeDisabled();
  });

  it("hides re-tailor when there is no flow to land in (dismiss remains)", () => {
    renderBanner({ canRetailor: false });
    expect(screen.queryByTestId("stale-cv-retailor")).toBeNull();
    expect(screen.getByTestId("stale-cv-dismiss")).toBeInTheDocument();
  });
});
