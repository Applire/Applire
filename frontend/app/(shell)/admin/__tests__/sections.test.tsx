// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUser } from "@/lib/auth";
import enMessages from "@/messages/en.json";
import { withIntl } from "@/lib/test-utils/with-intl";
import AdminLayout from "../layout";
import { ADMIN_SECTIONS, activeSection } from "../sections";

const replace = vi.fn();
let pathname = "/admin/users";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  usePathname: () => pathname,
}));
vi.mock("@/components/shell/AppTopbar", () => ({
  AppTopbar: ({ pageTitle }: { pageTitle?: string }) => <div data-testid="topbar">{pageTitle}</div>,
}));

const nav = enMessages.adminNav as Record<string, string>;

const admin: CurrentUser = {
  id: "u-1",
  email: "a@example.org",
  role: "admin",
  has_password: true,
  oidc_linked: false,
  ui_language: null,
};

function renderLayout(user: CurrentUser | null, status?: "authenticated" | "loading") {
  return render(withIntl(
    <CurrentUserProvider value={status ? { user, status } : { user }}>
      <AdminLayout>
        <p data-testid="page">page body</p>
      </AdminLayout>
    </CurrentUserProvider>,
  ));
}

beforeEach(() => {
  replace.mockReset();
  pathname = "/admin/users";
});
afterEach(cleanup);

describe("ADMIN_SECTIONS", () => {
  it("registers the Epic C sections in the approved order, the overview first (#694)", () => {
    expect(ADMIN_SECTIONS.map((s) => [s.key, s.href])).toEqual([
      ["overview", "/admin/overview"],
      ["users", "/admin/users"],
      ["usage", "/admin/usage"],
      ["settings", "/admin/settings"],
      ["audit", "/admin/audit"],
      ["appearance", "/admin/appearance"],
      ["monitoring", "/admin/monitoring"],
    ]);
  });

  it("every entry has a label in the adminNav catalog (en)", () => {
    for (const s of ADMIN_SECTIONS) expect(nav[s.labelKey]).toBeTruthy();
  });

  it("keys are unique and each href is /admin/<key>", () => {
    expect(new Set(ADMIN_SECTIONS.map((s) => s.key)).size).toBe(ADMIN_SECTIONS.length);
    for (const s of ADMIN_SECTIONS) expect(s.href).toBe(`/admin/${s.key}`);
  });
});

describe("activeSection", () => {
  it("exact href", () => {
    expect(activeSection("/admin/appearance").key).toBe("appearance");
  });
  it("longest-prefix: a nested path belongs to its section", () => {
    expect(activeSection("/admin/monitoring/tokens/1").key).toBe("monitoring");
    expect(activeSection("/admin/users/u-4").key).toBe("users");
  });
  it("a path that merely shares a string prefix does not match", () => {
    expect(activeSection("/admin/usersX").key).toBe(ADMIN_SECTIONS[0].key);
  });
  it("falls back to the first section for /admin, unknown, null and undefined", () => {
    expect(activeSection("/admin").key).toBe(ADMIN_SECTIONS[0].key);
    expect(activeSection("/admin/other").key).toBe(ADMIN_SECTIONS[0].key);
    expect(activeSection(null).key).toBe(ADMIN_SECTIONS[0].key);
    expect(activeSection(undefined).key).toBe(ADMIN_SECTIONS[0].key);
  });
});

describe("AdminLayout", () => {
  it("renders one tab per registry entry, linking to its href, with the catalog label", () => {
    renderLayout(admin);
    for (const s of ADMIN_SECTIONS) {
      const tab = screen.getByTestId(`admin-nav-${s.key}`);
      expect(tab).toHaveAttribute("href", s.href);
      expect(tab).toHaveTextContent(nav[s.labelKey]);
    }
    expect(screen.getAllByTestId(/^admin-nav-/)).toHaveLength(ADMIN_SECTIONS.length);
    expect(screen.getByRole("navigation", { name: "Administration" })).toBeInTheDocument();
    expect(screen.getByTestId("page")).toBeInTheDocument();
  });

  it.each(ADMIN_SECTIONS.map((s) => [s.key, s.href, s.labelKey]))(
    "on %s only that tab is aria-current and the topbar is titled with it",
    (key, href, labelKey) => {
      pathname = `${href}/something`;
      renderLayout(admin);
      for (const s of ADMIN_SECTIONS) {
        const tab = screen.getByTestId(`admin-nav-${s.key}`);
        if (s.key === key) expect(tab).toHaveAttribute("aria-current", "page");
        else expect(tab).not.toHaveAttribute("aria-current");
      }
      expect(screen.getByTestId("topbar")).toHaveTextContent(nav[labelKey]);
    },
  );

  it("a non-admin sees the 'for admins' card — no tabs, no section page (so no admin call)", () => {
    renderLayout({ ...admin, role: "user" });
    expect(screen.getByTestId("admin-forbidden")).toHaveTextContent(nav.forbiddenTitle);
    expect(screen.getByTestId("admin-forbidden-back")).toHaveAttribute("href", "/dashboard");
    expect(screen.queryAllByTestId(/^admin-nav-/)).toHaveLength(0);
    expect(screen.queryByTestId("page")).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("an admin is not redirected", () => {
    renderLayout(admin);
    expect(replace).not.toHaveBeenCalled();
  });

  it("while loading: no redirect, no tabs", () => {
    renderLayout(null, "loading");
    expect(replace).not.toHaveBeenCalled();
    expect(screen.queryAllByTestId(/^admin-nav-/)).toHaveLength(0);
  });
});
