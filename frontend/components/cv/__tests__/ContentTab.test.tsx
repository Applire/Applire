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
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ContentTab } from "../ContentTab";

const MOCK_SECTIONS = [
  {
    section_id: "introduction",
    label: "Introduction",
    content: "Experienced dev",
    has_override: false,
    gaps: [{ id: "Python", label: "Python" }],
  },
  {
    section_id: "skills",
    label: "Skills",
    content: "Python, React",
    has_override: false,
    gaps: [],
  },
];

const MOCK_FLOW_SUMMARY = {
  job_summary: "Senior Software Engineer",
  gap_summary: {
    gaps: [{ id: "Python", label: "Python" }],
    sections: MOCK_SECTIONS,
  },
  cv_summary: { sections: MOCK_SECTIONS },
};

const BASE_PROPS = {
  cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  flowSummary: MOCK_FLOW_SUMMARY,
  onSectionSave: vi.fn(),
  onUnsavedChange: vi.fn(),
};

describe("ContentTab", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sections: MOCK_SECTIONS, general_gaps: [] }),
    } as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Browse mode: renders gap count with role title", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() =>
      expect(screen.getByText(/1 gap found for "Senior Software Engineer"/)).toBeTruthy()
    );
  });

  it("Browse mode: renders section list with gap badges", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("Introduction")).toBeTruthy());
    expect(screen.getByText("Skills")).toBeTruthy();
    // Introduction has 1 gap
    expect(screen.getByText("1")).toBeTruthy();
  });

  it("Browse mode: clicking section transitions to Edit mode", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("Skills")).toBeTruthy());
    fireEvent.click(screen.getByText("Skills"));
    // Should show back button and section label
    expect(screen.getByTestId("back-to-browse")).toBeTruthy();
  });

  it("Browse mode: clicking gap card navigates to owning section", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("Python")).toBeTruthy());
    fireEvent.click(screen.getByText("Python"));
    expect(screen.getByTestId("back-to-browse")).toBeTruthy();
  });

  it("Edit mode: 'Back to overview' returns to Browse", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("Skills")).toBeTruthy());
    fireEvent.click(screen.getByText("Skills"));
    fireEvent.click(screen.getByTestId("back-to-browse"));
    await waitFor(() => expect(screen.getByText(/gap found/)).toBeTruthy());
  });
});
