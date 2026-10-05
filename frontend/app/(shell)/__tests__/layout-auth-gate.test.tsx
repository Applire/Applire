// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import { CurrentUserProvider, type CurrentUserValue } from "@/lib/auth/current-user";
import { withIntl } from "@/lib/test-utils/with-intl";

import ShellLayout from "../layout";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/flow/6f1c/cv",
}));

const STATE = { setup_required: false, oidc_enabled: false, oidc_button_label: "SSO", smtp_enabled: false, harness: false };
const ANNA = { id: "u", email: "anna.bauer@example.org", role: "admin" as const, has_password: true, oidc_linked: false, ui_language: "de" };

const realFetch = globalThis.fetch;
beforeEach(() => {
  replace.mockReset();
  globalThis.fetch = vi.fn(async () => new Response("{}", { status: 404 })) as unknown as typeof fetch;
});
afterEach(() => {
  globalThis.fetch = realFetch;
});

function renderShell(auth: Partial<CurrentUserValue>) {
  return render(
    withIntl(
      <CurrentUserProvider value={auth}>
        <ShellLayout>
          <p>page content</p>
        </ShellLayout>
      </CurrentUserProvider>,
      "de",
    ),
  );
}

describe("(shell) layout — auth gate (US330)", () => {
  it("signed out → /login?next=<where you were>, page never rendered", () => {
    renderShell({ status: "unauthenticated", authState: STATE });
    expect(replace).toHaveBeenCalledWith("/login?next=%2Fflow%2F6f1c%2Fcv");
    expect(screen.queryByText("page content")).toBeNull();
  });

  it("signed out on an unclaimed instance → /setup", () => {
    renderShell({ status: "unauthenticated", authState: { ...STATE, setup_required: true } });
    expect(replace).toHaveBeenCalledWith("/setup");
  });

  it("still loading → nothing rendered, no redirect", () => {
    renderShell({ status: "loading" });
    expect(screen.queryByText("page content")).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("signed in → the page", () => {
    renderShell({ user: ANNA, authState: STATE });
    expect(screen.getByText("page content")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-banner")).toBeNull();
  });

  it("backend unreachable → the page anyway (an outage is not a sign-out)", () => {
    renderShell({ status: "error" });
    expect(screen.getByText("page content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("harness → red banner above the shell", () => {
    renderShell({ user: ANNA, authState: { ...STATE, harness: true } });
    expect(screen.getByTestId("harness-banner").textContent).toContain(de.shell.harnessBanner);
  });
});
