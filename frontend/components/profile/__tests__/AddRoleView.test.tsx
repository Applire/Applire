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

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AddRoleView } from "../AddRoleView";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("@/lib/profile-roles", () => ({
  addRole: vi.fn().mockResolvedValue({
    profile_id: "p",
    new_role_id: "r",
    closed_role_ids: [],
    completeness_score: 0.85,
  }),
  markApplicationHired: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams("source=manual"),
  usePathname: () => "/profile/upload",
}));

describe("AddRoleView — manual mode", () => {
  it("renders title, company, start date inputs", () => {
    render(withIntl(<AddRoleView openRoles={[]} />, "en"));
    expect(screen.getByLabelText(/Job title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Company/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Start date/i)).toBeInTheDocument();
  });

  it("disables save until required fields are present", () => {
    render(withIntl(<AddRoleView openRoles={[]} />, "en"));
    const saveBtn = screen.getByRole("button", { name: /Save changes/i });
    expect(saveBtn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Job title/i), { target: { value: "Director" } });
    fireEvent.change(screen.getByLabelText(/Company/i), { target: { value: "Roche" } });
    fireEvent.change(screen.getByLabelText(/Start date/i), { target: { value: "2026-06-01" } });

    expect(saveBtn).not.toBeDisabled();
  });
});

describe("AddRoleView — jd_paste mode", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("renders JD textarea when source=jd_paste", async () => {
    vi.doMock("@/lib/profile-roles", () => ({
      addRole: vi.fn().mockResolvedValue({
        profile_id: "p",
        new_role_id: "r",
        closed_role_ids: [],
        completeness_score: 0.85,
      }),
      markApplicationHired: vi.fn(),
    }));
    vi.doMock("next/navigation", () => ({
      useRouter: () => ({ push: vi.fn() }),
      useSearchParams: () => new URLSearchParams("source=jd_paste"),
      usePathname: () => "/profile/upload",
    }));
    const { AddRoleView: View } = await import("../AddRoleView");
    render(withIntl(<View openRoles={[]} />, "en"));
    expect(screen.getByLabelText(/Paste the job description/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Analyse/i })).toBeInTheDocument();
  });
});

describe("AddRoleView — application pre-fill", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("pre-fills title/company from /api/applications/{id}", async () => {
    vi.doMock("@/lib/profile-roles", () => ({
      addRole: vi.fn(),
      markApplicationHired: vi.fn(),
    }));
    vi.doMock("next/navigation", () => ({
      useRouter: () => ({ push: vi.fn() }),
      useSearchParams: () => new URLSearchParams("source=application&application_id=abc"),
      usePathname: () => "/profile/upload",
    }));
    global.fetch = vi.fn(async (url) => {
      if (String(url).includes("/api/applications/abc")) {
        return new Response(JSON.stringify({
          id: "abc",
          role_title: "Director of QA",
          company_name: "Roche",
          flow_session_id: "f1",
        }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    const { AddRoleView: View } = await import("../AddRoleView");
    render(withIntl(<View openRoles={[]} />, "en"));
    expect(await screen.findByDisplayValue("Director of QA")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Roche")).toBeInTheDocument();
  });
});

describe("AddRoleView — close roles step", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.doMock("@/lib/profile-roles", () => ({
      addRole: vi.fn(),
      markApplicationHired: vi.fn(),
      fetchOpenRoles: vi.fn(),
    }));
    vi.doMock("next/navigation", () => ({
      useRouter: () => ({ push: vi.fn() }),
      useSearchParams: () => new URLSearchParams("source=manual"),
      usePathname: () => "/profile/upload",
    }));
  });

  const oneOpenRole = [{ id: "r1", company: "Acme", role: "Lead", start_date: "2023-01-01" }];
  const twoOpenRoles = [
    { id: "r1", company: "Acme", role: "Lead", start_date: "2023-01-01" },
    { id: "r2", company: "MyStartup", role: "Founder", start_date: "2025-04-01" },
  ];

  it("hides Step 2 when no open roles", async () => {
    const { AddRoleView: View } = await import("../AddRoleView");
    render(withIntl(<View openRoles={[]} />, "en"));
    expect(screen.queryByText(/Anything ending/i)).not.toBeInTheDocument();
  });

  it("checks single open role by default", async () => {
    const { AddRoleView: View } = await import("../AddRoleView");
    render(withIntl(<View openRoles={oneOpenRole} />, "en"));
    const cb = screen.getByLabelText(/Acme/) as HTMLInputElement;
    expect(cb.checked).toBe(true);
  });

  it("leaves all unchecked when multiple open roles", async () => {
    const { AddRoleView: View } = await import("../AddRoleView");
    render(withIntl(<View openRoles={twoOpenRoles} />, "en"));
    expect((screen.getByLabelText(/Acme/) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText(/MyStartup/) as HTMLInputElement).checked).toBe(false);
  });
});

describe("AddRoleView — fetches open roles when no prop supplied", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.doMock("@/lib/profile-roles", () => ({
      addRole: vi.fn(),
      markApplicationHired: vi.fn(),
      fetchOpenRoles: vi.fn().mockResolvedValue([
        { id: "r1", company: "Acme", role: "Lead", start_date: "2023-01-01" },
      ]),
    }));
    vi.doMock("next/navigation", () => ({
      useRouter: () => ({ push: vi.fn() }),
      useSearchParams: () => new URLSearchParams("source=manual"),
      usePathname: () => "/profile/upload",
    }));
  });

  it("renders the fetched open role in Step 2", async () => {
    const { AddRoleView: View } = await import("../AddRoleView");
    render(withIntl(<View />, "en"));
    expect(await screen.findByLabelText(/Acme/)).toBeInTheDocument();
  });
});
