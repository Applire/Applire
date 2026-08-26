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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProfilePage from "../page";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/profile",
}));

vi.mock("@/lib/providers/locale-provider", () => ({
  useLocale: () => ({ locale: "en", setLocale: vi.fn() }),
}));

// The drawers drive the real session engine — stub them so we can drive onClose.
vi.mock("@/components/profile/ProfileReviewDrawer", () => ({
  ProfileReviewDrawer: ({ open, onClose }: { open: boolean; onClose: () => void }) =>
    open ? (
      <button data-testid="stub-review-close" onClick={onClose}>
        close-review
      </button>
    ) : null,
}));
vi.mock("@/components/profile/EnrichmentDrawer", () => ({
  EnrichmentDrawer: ({ open, scope }: { open: boolean; scope?: string }) =>
    open ? <div data-testid="stub-enrich-scope">{scope ?? "all"}</div> : null,
}));
vi.mock("@/components/profile/PhotoManager", () => ({
  PhotoManager: () => null,
}));

const PROFILE = {
  id: "p1",
  profile: {
    personal_info: { name: "Sven Hartmann", email: "sven@example.com" },
    professional_summary: { de: null, en: "Backend/platform engineer, 11 years." },
    work_experience: [
      {
        id: "e43fc4b2-aaaa",
        role: "Senior Software Engineer",
        company: "Logivia",
        start_date: "2020-03",
        end_date: null,
        achievements: ["Cut build times by 40%"],
        source: "cv_upload",
        role_aliases: ["Engineering Lead"],
      },
    ],
    skills: [{ name: "Kubernetes", proficiency: "advanced", source: "work:Logivia" }],
    education: [],
    languages: [],
    certifications: [],
    // US292 — the last three list sections and their internal plumbing.
    projects: [
      {
        id: "proj-1",
        name: "CI/CD Migration",
        role: "Lead Developer",
        start_date: "2022-01",
        end_date: "2022-06",
        achievements: ["Reduced average build time by 78%"],
        expected_fields: ["team_size"],
      },
    ],
    publications: [{ id: "pub-1", title: "Zero-downtime schema migrations", type: "publication" }],
    volunteer_activities: [
      { id: "vol-1", organization: "Hackerspace München", role: "Mentor", cause: "Education" },
    ],
  },
  completeness: 0.99,
  merge_conflicts: [],
  created_at: "2026-06-24T00:00:00Z",
  updated_at: "2026-06-24T00:00:00Z",
};

// #382 (PO decision 2026-08-08, Option A) — the budget figure is in the vault
// but omitted from every generated document because it states no unit. The PO
// condition on that omission: it is addressed to the user AT THE FIELD on the
// master profile page, not only in the Health hub.
const UNIT_ISSUE = {
  id: "unit:budget_managed:Senior Software Engineer @ Logivia",
  thread: "unit",
  profile_mismatch_severity: "review",
  summary: "budget_managed: '6000000' states no unit, so it is omitted",
  field_ref: "work_experience.budget_managed",
  source_record_ref: "Senior Software Engineer @ Logivia",
};

const HEALTH = {
  issues: [
    {
      id: "conflict:1",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary: "start_date '2020-03' vs '2023-01'",
      field_ref: "start_date",
      source_record_ref: "rec-1",
    },
  ],
  completeness: { score: 0.99, gaps: [], field_gaps: [] },
};

function mockFetch(health: unknown = HEALTH) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/profile/health"))
      return { ok: true, json: async () => health };
    if (url.includes("/api/profile/enrichment-history"))
      return { ok: true, json: async () => [] };
    if (url.includes("/api/profile"))
      return { ok: true, json: async () => PROFILE };
    return { ok: true, json: async () => ({}) };
  });
}

describe("ProfilePage", () => {
  beforeEach(() => {
    global.fetch = mockFetch() as unknown as typeof fetch;
  });

  // F8 (#76): sections render as readable fields, never raw JSON with internal ids.
  it("renders structured profile fields and hides internal plumbing", async () => {
    render(withIntl(<ProfilePage />, "en"));

    await waitFor(() =>
      expect(screen.getAllByText("Senior Software Engineer").length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/Cut build times by 40%/)).toBeInTheDocument();
    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    // English summary surfaces even though the German one is null (F9.2).
    expect(
      screen.getByText("Backend/platform engineer, 11 years."),
    ).toBeInTheDocument();
    // Internal fields never leak.
    expect(screen.queryByText(/e43fc4b2/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cv_upload/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/role_aliases/);
  });

  // F5 (#73): the health panel must refetch after a resolve, not stay stale.
  // E055 / US292 (JF-F-H3.3): the whole-section JSON textarea is retired —
  // every editable section offers a structured editor and the three sections
  // that never had a card (projects, publications, volunteering) render as
  // readable cards with their internal plumbing hidden.
  it("offers a structured editor for every editable section and no JSON textarea", async () => {
    render(withIntl(<ProfilePage />, "en"));

    await waitFor(() => expect(screen.getByText("CI/CD Migration")).toBeInTheDocument());
    expect(screen.getByText("Zero-downtime schema migrations")).toBeInTheDocument();
    expect(screen.getByText(/Hackerspace München/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/expected_fields|proj-1|pub-1|vol-1/);

    for (const testId of [
      "summary-edit",
      "personal-info-edit",
      "work-experience-add",
      "certifications-add",
      "projects-add",
      "publications-add",
      "volunteer-add",
    ]) {
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    }
    // The retired path: no whole-section textarea, no section-level "Edit"
    // button that would serialise a section to JSON.
    expect(document.querySelector("textarea")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it("refetches profile health after the review drawer closes", async () => {
    const fetchMock = mockFetch();
    global.fetch = fetchMock as unknown as typeof fetch;

    render(withIntl(<ProfilePage />, "en"));

    await waitFor(() => expect(screen.getByTestId("health-panel")).toBeInTheDocument());

    const healthCallsBefore = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("/api/profile/health"),
    ).length;

    // Open then close the review drawer (simulating a resolution round-trip).
    fireEvent.click(screen.getByTestId("health-resolve"));
    fireEvent.click(screen.getByTestId("stub-review-close"));

    await waitFor(() => {
      const after = fetchMock.mock.calls.filter((c) =>
        String(c[0]).includes("/api/profile/health"),
      ).length;
      expect(after).toBeGreaterThan(healthCallsBefore);
    });
  });

  describe("budget-unit omission (#382)", () => {
    const UNIT_HEALTH = {
      issues: [UNIT_ISSUE],
      completeness: { score: 0.9, gaps: [], field_gaps: [] },
    };

    it("shows the fix affordance on the affected work entry", async () => {
      global.fetch = mockFetch(UNIT_HEALTH) as unknown as typeof fetch;
      render(withIntl(<ProfilePage />, "en"));

      await waitFor(() =>
        expect(screen.getByTestId("budget-unit-hint")).toBeInTheDocument(),
      );
      // Names the entry, so the hint is unambiguous on a multi-role profile.
      expect(screen.getByTestId("budget-unit-hint").textContent).toContain(
        "Senior Software Engineer",
      );
    });

    it("offers the fix where the data lives — scoped to that entry", async () => {
      global.fetch = mockFetch(UNIT_HEALTH) as unknown as typeof fetch;
      render(withIntl(<ProfilePage />, "en"));

      await waitFor(() =>
        expect(screen.getByTestId("budget-unit-hint")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByTestId("budget-unit-hint"));

      await waitFor(() =>
        expect(screen.getByTestId("stub-enrich-scope").textContent).toBe(
          "work_experience:Logivia:Senior Software Engineer",
        ),
      );
    });

    it("shows nothing when every budget states its unit", async () => {
      render(withIntl(<ProfilePage />, "en"));

      await waitFor(() =>
        expect(screen.getAllByText("Senior Software Engineer").length).toBeGreaterThan(0),
      );
      expect(screen.queryByTestId("budget-unit-hint")).not.toBeInTheDocument();
    });
  });
});
