// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * M5 (UX audit 2026-10-20) — one seam test per shell call site that navigates
 * programmatically. Each one, while a page holds an unsaved draft, must hand
 * the target to that page's guard instead of pushing it: Felix edited the
 * introduction, clicked the sidebar's "Dashboard" and lost the draft, because
 * the sidebar navigates with router.push buttons the link guard never saw.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AppSidebar } from "../AppSidebar";
import { MobileNavDrawer } from "../MobileNavDrawer";
import { UserMenu } from "../UserMenu";
import { ShellUserProvider } from "../ShellUserContext";
import { useUnsavedDraftGuard, type GuardedNav } from "@/components/document/useUnsavedDraftGuard";

const mockPush = vi.fn();
let mockPathname = "/flow/f1/cv";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => mockPathname,
}));

vi.mock("@/lib/auth/current-user", () => ({
  useCurrentUser: () => ({
    user: { email: "anna.bauer@example.org", role: "admin" },
    isAdmin: true,
    signOut: vi.fn(),
  }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

/** A page holding an unsaved draft (the CV page's Bearbeiten tab, reduced). */
function DirtyPage({ dirty, onAttempt }: { dirty: boolean; onAttempt: (n: GuardedNav) => void }) {
  useUnsavedDraftGuard(dirty, onAttempt);
  return null;
}

describe("shell navigation goes past the unsaved-draft guard (M5)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockPathname = "/flow/f1/cv";
  });

  it("AppSidebar (full): a nav button while dirty asks the page, does not push", () => {
    mockPathname = "/flow/f1/gaps"; // not a document route → full sidebar
    const onAttempt = vi.fn();
    render(
      <>
        <DirtyPage dirty onAttempt={onAttempt} />
        <AppSidebar />
      </>,
    );
    fireEvent.click(screen.getByTestId("sidebar-nav-dashboard"));
    expect(mockPush).not.toHaveBeenCalled();
    expect(onAttempt).toHaveBeenCalledWith({ kind: "href", href: "/dashboard" });
  });

  it("AppSidebar (full): clean, the same button navigates", () => {
    mockPathname = "/flow/f1/gaps";
    const onAttempt = vi.fn();
    render(
      <>
        <DirtyPage dirty={false} onAttempt={onAttempt} />
        <AppSidebar />
      </>,
    );
    fireEvent.click(screen.getByTestId("sidebar-nav-dashboard"));
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
    expect(onAttempt).not.toHaveBeenCalled();
  });

  it("AppSidebar (rail, the CV page's own shape): an icon button while dirty asks the page", () => {
    const onAttempt = vi.fn();
    render(
      <>
        <DirtyPage dirty onAttempt={onAttempt} />
        <AppSidebar />
      </>,
    );
    expect(screen.getByTestId("app-sidebar")).toHaveAttribute("data-variant", "rail");
    fireEvent.click(screen.getByRole("button", { name: "documents" }));
    expect(mockPush).not.toHaveBeenCalled();
    expect(onAttempt).toHaveBeenCalledWith({ kind: "href", href: "/documents" });
  });

  it("MobileNavDrawer: a nav item while dirty asks the page", () => {
    const onAttempt = vi.fn();
    render(
      <ShellUserProvider userName={null}>
        <DirtyPage dirty onAttempt={onAttempt} />
        <MobileNavDrawer open onOpenChange={vi.fn()} />
      </ShellUserProvider>,
    );
    fireEvent.click(screen.getByTestId("nav-item-profile"));
    expect(mockPush).not.toHaveBeenCalled();
    expect(onAttempt).toHaveBeenCalledWith({ kind: "href", href: "/profile" });
  });

  it("UserMenu: the account entry while dirty asks the page", () => {
    const onAttempt = vi.fn();
    render(
      <>
        <DirtyPage dirty onAttempt={onAttempt} />
        <UserMenu>{"AB"}</UserMenu>
      </>,
    );
    fireEvent.click(screen.getByTestId("user-menu-trigger"));
    fireEvent.click(screen.getByTestId("user-menu-account"));
    expect(mockPush).not.toHaveBeenCalled();
    expect(onAttempt).toHaveBeenCalledWith({ kind: "href", href: "/settings#account" });
  });
});
