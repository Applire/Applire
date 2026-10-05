// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, useCurrentUser } from "../current-user";
import { getStorageUserId, setStorageUserId, userScopedKey } from "../storage";

function Probe() {
  const { status, user, isAdmin, authState } = useCurrentUser();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="email">{user?.email ?? ""}</span>
      <span data-testid="admin">{String(isAdmin)}</span>
      <span data-testid="harness">{String(authState?.harness ?? "")}</span>
    </div>
  );
}

const ME = {
  id: "u-1",
  email: "anna.bauer@example.org",
  role: "admin",
  has_password: true,
  oidc_linked: false,
  ui_language: "de",
};
const STATE = { setup_required: false, oidc_enabled: false, oidc_button_label: "SSO", smtp_enabled: false, harness: true };

function mockFetch(meStatus: number | "network") {
  return vi.fn(async (url: string) => {
    if (url.endsWith("/api/auth/state")) return new Response(JSON.stringify(STATE), { status: 200 });
    if (meStatus === "network") throw new TypeError("Failed to fetch");
    if (meStatus === 200) return new Response(JSON.stringify(ME), { status: 200 });
    return new Response(JSON.stringify({ detail: { error_code: "unauthenticated", message: "x" } }), { status: meStatus });
  });
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  setStorageUserId(null);
});

describe("CurrentUserProvider / useCurrentUser", () => {
  it("exposes the signed-in person, role and instance state", async () => {
    globalThis.fetch = mockFetch(200) as unknown as typeof fetch;
    render(<CurrentUserProvider><Probe /></CurrentUserProvider>);
    expect(screen.getByTestId("status").textContent).toBe("loading");
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("email").textContent).toBe("anna.bauer@example.org");
    expect(screen.getByTestId("admin").textContent).toBe("true");
    expect(screen.getByTestId("harness").textContent).toBe("true");
    expect(getStorageUserId()).toBe("u-1");
    expect(userScopedKey("applire.x")).toBe("applire.x.u.u-1");
  });

  it("401 → unauthenticated", async () => {
    globalThis.fetch = mockFetch(401) as unknown as typeof fetch;
    render(<CurrentUserProvider><Probe /></CurrentUserProvider>);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));
    expect(getStorageUserId()).toBeNull();
  });

  it("an outage is 'error', never a sign-out", async () => {
    globalThis.fetch = mockFetch("network") as unknown as typeof fetch;
    render(<CurrentUserProvider><Probe /></CurrentUserProvider>);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("error"));
    globalThis.fetch = mockFetch(502) as unknown as typeof fetch;
  });

  it("a static value fetches nothing and derives isAdmin from the role", () => {
    const spy = vi.fn();
    globalThis.fetch = spy as unknown as typeof fetch;
    render(
      <CurrentUserProvider value={{ user: { ...ME, role: "user" } as never }}>
        <Probe />
      </CurrentUserProvider>,
    );
    expect(screen.getByTestId("status").textContent).toBe("authenticated");
    expect(screen.getByTestId("admin").textContent).toBe("false");
    expect(spy).not.toHaveBeenCalled();
  });

  it("without a provider: loading, nobody", () => {
    render(<Probe />);
    expect(screen.getByTestId("status").textContent).toBe("loading");
    expect(screen.getByTestId("email").textContent).toBe("");
  });
});
