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
import { render, screen, fireEvent } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/de.json";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { ShellUserProvider } from "@/components/shell/ShellUserContext";
import { CurrentUserProvider, type CurrentUserValue } from "@/lib/auth/current-user";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

function withIntl(c: React.ReactNode, userName: string | null = null, auth: Partial<CurrentUserValue> = { status: "loading" }) {
  return (
    <NextIntlClientProvider locale="de" messages={messages}>
      <CurrentUserProvider value={auth}>
        <ShellUserProvider userName={userName}>{c}</ShellUserProvider>
      </CurrentUserProvider>
    </NextIntlClientProvider>
  );
}

const JONAS = {
  id: "u-2",
  email: "jonas.keller@example.org",
  role: "user" as const,
  has_password: true,
  oidc_linked: false,
  ui_language: "de",
};
const ANNA = { ...JONAS, id: "u-1", email: "anna.bauer@example.org", role: "admin" as const };

describe("AppTopbar", () => {
  it("section mode renders a single h1 with the section title", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />));
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe(messages.shell.dashboard);
  });

  it("detail mode renders a back link + page title", () => {
    render(withIntl(
      <AppTopbar mode="detail" backHref="/dashboard" backLabelKey="shell.dashboard" pageTitle="Senior QA Manager" />
    ));
    expect(screen.getByRole("link", { name: /← .*Dashboard/i })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Senior QA Manager");
  });

  it("flow mode renders the wizard stepper instead of a title", () => {
    render(withIntl(
      <AppTopbar mode="flow" steps={[
        { key: "cv_import",     labelKey: "flow.stepProfile",   state: "done"    },
        { key: "gap_analysis",  labelKey: "flow.stepGaps",      state: "active"  },
        { key: "interview",     labelKey: "flow.stepInterview", state: "pending" },
        { key: "cv_generation", labelKey: "flow.stepCV",        state: "pending" },
      ]} />
    ));
    expect(screen.getByText(messages.flow.stepProfile)).toBeInTheDocument();
    expect(screen.getByText(messages.flow.stepGaps)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  // US223: below md the persistent AppSidebar is hidden, so every AppTopbar
  // mode needs its own hamburger affordance into the equivalent drawer nav.
  it("renders a hamburger button that opens the mobile nav drawer", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />));
    expect(screen.queryByTestId("mobile-nav-drawer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: messages.shell.openNavAriaLabel }));
    expect(screen.getByTestId("mobile-nav-drawer")).toBeInTheDocument();
  });

  it("renders the hamburger in every topbar mode (detail, flow)", () => {
    const { unmount } = render(withIntl(
      <AppTopbar mode="detail" backHref="/dashboard" backLabelKey="shell.dashboard" pageTitle="Senior QA Manager" />
    ));
    expect(screen.getByRole("button", { name: messages.shell.openNavAriaLabel })).toBeInTheDocument();
    unmount();

    render(withIntl(
      <AppTopbar mode="flow" steps={[
        { key: "cv_import", labelKey: "flow.stepProfile", state: "active" },
      ]} />
    ));
    expect(screen.getByRole("button", { name: messages.shell.openNavAriaLabel })).toBeInTheDocument();
  });

  it("shows the fallback avatar letter on the mobile avatar when no userName has loaded yet", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />));
    expect(screen.getByTestId("topbar-avatar-mobile").textContent).toBe(messages.shell.topbarUserInitial);
  });

  it("shows the real user's initials on the mobile avatar once userName is threaded via context", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, "Max Mustermann"));
    expect(screen.getByTestId("topbar-avatar-mobile").textContent).toBe("MM");
  });

  // US330 (W0-B user-menu mock, G-1): the desktop avatar is the account-menu
  // button now and shows the person's letter — the US223 placeholder stays only
  // while nobody is known.
  it("desktop avatar: placeholder while nobody is known, then the person's letter", () => {
    const { unmount } = render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />));
    expect(screen.getByTestId("topbar-avatar-desktop").textContent).toBe(messages.shell.topbarUserInitial);
    unmount();
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, "Max Mustermann", { user: JONAS }));
    expect(screen.getByTestId("topbar-avatar-desktop").textContent).toBe("M");
  });

  it("desktop avatar falls back to the e-mail's first letter without a profile name", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, null, { user: JONAS }));
    expect(screen.getByTestId("topbar-avatar-desktop").textContent).toBe("J");
    expect(screen.getByTestId("topbar-avatar-mobile").textContent).toBe("J");
  });

  describe("account menu (US330)", () => {
    it("admin: signed in as + role chip, account, Administration, sign out", () => {
      render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, null, { user: ANNA }));
      const trigger = screen.getByRole("button", { name: messages.shell.userMenuAria });
      expect(trigger).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(trigger);
      expect(trigger).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByText(messages.shell.userMenuSignedInAs)).toBeInTheDocument();
      expect(screen.getByTestId("user-menu-email").textContent).toBe("anna.bauer@example.org");
      expect(screen.getByTestId("user-menu-role").textContent).toBe(messages.shell.roleAdmin);
      expect(screen.getByRole("menuitem", { name: new RegExp(messages.shell.userMenuAccount) })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: new RegExp(messages.shell.userMenuAdmin) })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: new RegExp(messages.shell.userMenuSignOut) })).toBeInTheDocument();
    });

    it("user: no Administration entry, role Nutzer", () => {
      render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, null, { user: JONAS }));
      fireEvent.click(screen.getByRole("button", { name: messages.shell.userMenuAria }));
      expect(screen.getByTestId("user-menu-role").textContent).toBe(messages.shell.roleUser);
      expect(screen.queryByTestId("user-menu-admin")).toBeNull();
    });

    it("sign out calls signOut and shows the working label", () => {
      const signOut = vi.fn(() => new Promise<void>(() => {}));
      render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, null, { user: JONAS, signOut }));
      fireEvent.click(screen.getByRole("button", { name: messages.shell.userMenuAria }));
      fireEvent.click(screen.getByTestId("user-menu-sign-out"));
      expect(signOut).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId("user-menu-sign-out").textContent).toContain(messages.shell.signingOut);
    });

    it("Escape closes the menu", () => {
      render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, null, { user: JONAS }));
      fireEvent.click(screen.getByRole("button", { name: messages.shell.userMenuAria }));
      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByTestId("user-menu")).toBeNull();
    });
  });
});
