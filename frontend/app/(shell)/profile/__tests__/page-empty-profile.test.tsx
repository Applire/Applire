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

/**
 * #704 (founder UAT, 2026-09-15) — a first-time user with no Master Profile
 * saw "No profile found. Please import a CV first." + "Back to Home", with no
 * way to actually import a CV from that page. Reuses the same upload
 * component the welcome screen already offers (`ProfileImportView`, already
 * optional-flowId — it works standalone at `/profile/upload`) instead of
 * redirecting away.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ProfilePage from "../page";
import { withIntl } from "@/lib/test-utils/with-intl";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/profile",
}));

vi.mock("@/lib/providers/locale-provider", () => ({
  useLocale: () => ({ locale: "en", setLocale: vi.fn() }),
}));

// Stub the real dropzone component — it has its own full test suite
// (ProfileImportView.test.tsx). Here we only need to see that the EMPTY
// STATE mounts it (with hideTopbar) and can drive its onImported callback.
vi.mock("@/components/profile/ProfileImportView", () => ({
  ProfileImportView: ({
    hideTopbar,
    onImported,
  }: {
    hideTopbar?: boolean;
    onImported?: () => void;
  }) => (
    <div data-testid="stub-import-view" data-hide-topbar={String(!!hideTopbar)}>
      <button data-testid="stub-import-success" onClick={() => onImported?.()}>
        simulate-success
      </button>
    </div>
  ),
}));

const PROFILE = {
  id: "p1",
  profile: {
    personal_info: { name: "Priya Nair" },
    work_experience: [],
    education: [],
    skills: [],
    languages: [],
    certifications: [],
  },
  completeness: 0.1,
  merge_conflicts: [],
  created_at: "2026-09-15T00:00:00Z",
  updated_at: "2026-09-15T00:00:00Z",
};

function mockFetch(profileOk: boolean) {
  let profileCalls = 0;
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/profile/health"))
      return { ok: true, json: async () => ({ issues: [], completeness: { score: 0, gaps: [], field_gaps: [] } }) };
    if (url.includes("/api/profile/enrichment-history"))
      return { ok: true, json: async () => [] };
    if (url.includes("/api/profile")) {
      profileCalls += 1;
      // First call fails (no profile); once profileOk flips true (after the
      // simulated import), a later call succeeds.
      const ok = profileOk && profileCalls > 1;
      return ok
        ? { ok: true, json: async () => PROFILE }
        : { ok: false, json: async () => ({}) };
    }
    return { ok: true, json: async () => ({}) };
  });
}

describe("ProfilePage — empty state (#704)", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("shows a heading, the inline upload view (no topbar), and a dashboard link instead of a dead end", async () => {
    global.fetch = mockFetch(false) as unknown as typeof fetch;
    render(withIntl(<ProfilePage />, "en"));

    const importView = await screen.findByTestId("stub-import-view");
    expect(importView.dataset.hideTopbar).toBe("true");
    expect(screen.getByText(/no profile yet/i)).toBeInTheDocument();

    const backButton = screen.getByRole("button", { name: /back to home/i });
    fireEvent.click(backButton);
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("re-fetches and shows the profile after a successful import, with no full reload", async () => {
    global.fetch = mockFetch(true) as unknown as typeof fetch;
    render(withIntl(<ProfilePage />, "en"));

    await screen.findByTestId("stub-import-view");
    fireEvent.click(screen.getByTestId("stub-import-success"));

    await waitFor(() =>
      expect(screen.queryByTestId("stub-import-view")).not.toBeInTheDocument(),
    );
    expect(screen.getAllByText("Priya Nair").length).toBeGreaterThan(0);
  });

  it("renders the German heading under the de locale", async () => {
    global.fetch = mockFetch(false) as unknown as typeof fetch;
    render(withIntl(<ProfilePage />, "de"));

    await screen.findByTestId("stub-import-view");
    expect(screen.getByText(/noch kein profil/i)).toBeInTheDocument();
  });
});
