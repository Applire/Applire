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
  EnrichmentDrawer: () => null,
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
  },
  completeness: 0.99,
  merge_conflicts: [],
  created_at: "2026-06-24T00:00:00Z",
  updated_at: "2026-06-24T00:00:00Z",
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
  completeness: { score: 0.99, gaps: [] },
};

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/profile/health"))
      return { ok: true, json: async () => HEALTH };
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
});
