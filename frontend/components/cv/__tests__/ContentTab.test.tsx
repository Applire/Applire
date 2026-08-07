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

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));

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

  // #311: the backend labels the two static sections in English ("Introduction",
  // "Skills"). The German UI must not show them untranslated.
  it("Browse mode: static section labels are localised, de", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />, "de"));
    await waitFor(() => expect(screen.getByText("Einleitung")).toBeTruthy());
    expect(screen.getByText("Fähigkeiten")).toBeTruthy();
    expect(screen.queryByText("Introduction")).toBeNull();
    expect(screen.queryByText("Skills")).toBeNull();
  });

  it("Browse mode: position section labels keep the backend value", async () => {
    const positionSections = [
      {
        section_id: "position::11111111-1111-1111-1111-111111111111",
        label: "Senior Engineer — SAP",
        content: "Built things",
        has_override: false,
        gaps: [],
      },
    ];
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sections: positionSections, general_gaps: [] }),
    } as Response);
    render(withIntl(<ContentTab {...BASE_PROPS} />, "de"));
    await waitFor(() => expect(screen.getByText("Senior Engineer — SAP")).toBeTruthy());
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

  // #117: an honest gap routes to the profile hub — never into the CV editor.
  it("Browse mode: clicking an honest gap card routes to the profile hub", async () => {
    const honestSections = [
      {
        ...MOCK_SECTIONS[0],
        gaps: [{ id: "DevSecOps", label: "DevSecOps", kind: "honest" }],
      },
      MOCK_SECTIONS[1],
    ];
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sections: honestSections, general_gaps: [] }),
    } as Response);
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("DevSecOps")).toBeTruthy());
    fireEvent.click(screen.getByText("DevSecOps"));
    expect(mockPush).toHaveBeenCalledWith("/profile");
    expect(screen.queryByTestId("back-to-browse")).toBeNull();
  });

  it("Edit mode: 'Back to overview' returns to Browse", async () => {
    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("Skills")).toBeTruthy());
    fireEvent.click(screen.getByText("Skills"));
    fireEvent.click(screen.getByTestId("back-to-browse"));
    await waitFor(() => expect(screen.getByText(/gap found/)).toBeTruthy());
  });

  it("Edit mode: 'Apply' on a Kaile suggestion saves it into the section (Task 11)", async () => {
    sessionStorage.setItem("finetune_save_scope", "cv");
    const patchBodies: string[] = [];
    vi.spyOn(global, "fetch").mockImplementation((async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/rewrite")) {
        return { ok: true, json: async () => ({ suggestion: "Kaile improved text" }) } as Response;
      }
      if (init?.method === "PATCH" && /\/sections\//.test(u)) {
        patchBodies.push(String(init.body));
        return {
          ok: true,
          json: async () => ({ html: "<html/>", overrides_applied: [], resolved_gaps: [] }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({ sections: MOCK_SECTIONS, general_gaps: [] }),
      } as Response;
    }) as typeof fetch);

    render(withIntl(<ContentTab {...BASE_PROPS} />));
    await waitFor(() => expect(screen.getByText("Introduction")).toBeTruthy());
    fireEvent.click(screen.getByText("Introduction")); // enter edit mode

    fireEvent.change(screen.getByTestId("kaile-directions-input"), {
      target: { value: "make it punchy" },
    });
    fireEvent.click(screen.getByTestId("kaile-rewrite-btn"));
    await waitFor(() => expect(screen.getByTestId("apply-suggestion-btn")).toBeTruthy());
    fireEvent.click(screen.getByTestId("apply-suggestion-btn"));

    await waitFor(() =>
      expect(patchBodies.some((b) => b.includes("Kaile improved text"))).toBe(true)
    );
    sessionStorage.clear();
  });

  it("Browse mode: deduplicates a gap repeated across sections (bug 4)", async () => {
    // Simulate the pre-fix backend behaviour where the same gap id was emitted
    // under multiple sections — the count and chip list must not double-count.
    const DUP_SECTIONS = [
      {
        section_id: "introduction",
        label: "Introduction",
        content: "x",
        has_override: false,
        gaps: [
          { id: "Industrial Design", label: "Industrial Design" },
          { id: "IoT", label: "IoT" },
        ],
      },
      {
        section_id: "skills",
        label: "Skills",
        content: "y",
        has_override: false,
        gaps: [{ id: "Industrial Design", label: "Industrial Design" }],
      },
    ];
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sections: DUP_SECTIONS, general_gaps: [] }),
    } as Response);

    render(withIntl(<ContentTab {...BASE_PROPS} />));
    // 2 distinct gaps, not 3
    await waitFor(() =>
      expect(screen.getByText(/2 gaps found for "Senior Software Engineer"/)).toBeTruthy()
    );
    // The repeated gap renders exactly one chip
    expect(screen.getAllByText("Industrial Design")).toHaveLength(1);
  });
});
