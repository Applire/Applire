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

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

function withIntl(c: React.ReactNode, userName: string | null = null) {
  return (
    <NextIntlClientProvider locale="de" messages={messages}>
      <ShellUserProvider userName={userName}>{c}</ShellUserProvider>
    </NextIntlClientProvider>
  );
}

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

  it("shows the fallback avatar letter when no userName has loaded yet", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />));
    expect(screen.getByRole("button", { name: messages.shell.openSettingsAriaLabel }).textContent).toBe(
      messages.shell.topbarUserInitial
    );
  });

  it("shows the real user's initials on the avatar once userName is threaded via context", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />, "Max Mustermann"));
    expect(screen.getByRole("button", { name: messages.shell.openSettingsAriaLabel }).textContent).toBe("MM");
  });
});
