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

// US224 — mobile dashboard: the applications card grid (grid-cols-2) must
// stack to a single column below md. The Quick Tailor / Profile Strength row
// above it already stacks (US223, a1a6b83) — this covers the remaining grid.

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import DashboardPage from "../page";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/dashboard",
}));

vi.mock("@/components/dashboard/QuickTailorWidget", () => ({
  QuickTailorWidget: () => null,
}));
vi.mock("@/components/dashboard/ProfileStrengthCard", () => ({
  ProfileStrengthCard: () => null,
}));
vi.mock("@/components/dashboard/ImportInProgressBanner", () => ({
  ImportInProgressBanner: () => null,
}));

const APPLICATIONS = [
  {
    id: "app-1",
    role_title: "Senior QA Manager",
    company_name: "DataCraft GmbH",
    workflow_status: "analyzing",
    user_status: "tracking",
    flow_session_id: "flow-1",
    updated_at: "2026-07-14T09:00:00Z",
  },
  {
    id: "app-2",
    role_title: "Platform Engineer",
    company_name: "Example AG",
    workflow_status: "completed",
    user_status: "tracking",
    flow_session_id: "flow-2",
    updated_at: "2026-07-13T09:00:00Z",
  },
];

function mockFetchOnce() {
  global.fetch = vi.fn((url: string) => {
    if (url.includes("/api/applications")) {
      return Promise.resolve({ ok: true, json: async () => ({ items: APPLICATIONS }) });
    }
    if (url.includes("/api/profile")) {
      return Promise.resolve({ ok: true, json: async () => ({ profile: {} }) });
    }
    return Promise.resolve({ ok: false, json: async () => ({}) });
  }) as unknown as typeof fetch;
}

describe("DashboardPage (US224 mobile grid)", () => {
  it("stacks the applications grid to one column below md, two at md and up", async () => {
    mockFetchOnce();
    render(withIntl(<DashboardPage />));
    await waitFor(() => screen.getByTestId("applications-grid"));
    const grid = screen.getByTestId("applications-grid");
    expect(grid.className).toContain("grid-cols-1");
    expect(grid.className).toContain("md:grid-cols-2");
  });
});
