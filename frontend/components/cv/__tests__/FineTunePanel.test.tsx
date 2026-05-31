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

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { FineTunePanel } from "../FineTunePanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const MOCK_SECTIONS_RESPONSE = {
  sections: [
    {
      section_id: "introduction",
      label: "Introduction",
      content: "Original intro",
      has_override: false,
      gaps: [{ id: "Python", label: "Python" }],
    },
    {
      section_id: "skills",
      label: "Skills",
      content: "Java\nSQL",
      has_override: false,
      gaps: [],
    },
  ],
  general_gaps: [],
};

const BASE_PROPS = {
  cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  initialHtml: "<html><body>CV</body></html>",
  onClose: vi.fn(),
};

describe("FineTunePanel", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => MOCK_SECTIONS_RESPONSE,
    } as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading skeleton while fetching sections", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    expect(document.querySelectorAll("[data-testid='section-skeleton']").length).toBeGreaterThan(0);
  });

  it("renders section list items after loading", async () => {
    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    const items = await screen.findAllByTestId("section-list-item");
    expect(items).toHaveLength(2);
    expect(screen.getByText("Introduction")).toBeTruthy();
    expect(screen.getByText("Skills")).toBeTruthy();
  });

  it("shows gap badge with count for sections with gaps", async () => {
    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    await screen.findAllByTestId("section-list-item");
    const badge = screen.getByTestId("gap-badge");
    expect(badge.textContent).toBe("1");
  });

  it("shows retry button on fetch error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    await screen.findByText("Try again");
  });

  it("shows 'all gaps closed' when all section gaps are empty", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        sections: [
          {
            section_id: "introduction",
            label: "Introduction",
            content: "text",
            has_override: false,
            gaps: [],
          },
        ],
        general_gaps: [],
      }),
    } as Response);
    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    await screen.findByTestId("all-gaps-closed");
  });

  it("gap badge is shown for sections with gaps", async () => {
    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    await screen.findAllByTestId("section-list-item");
    expect(screen.getByTestId("gap-badge")).toBeTruthy();
  });

  it("renders mobile-accordion when matchMedia returns mobile", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("768"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });

    render(withIntl(<FineTunePanel {...BASE_PROPS} />));
    await screen.findAllByTestId("section-list-item");
    expect(document.querySelector("[data-testid='mobile-accordion']")).toBeTruthy();
  });
});
