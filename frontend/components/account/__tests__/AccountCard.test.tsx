// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type AuthState, type CurrentUser } from "@/lib/auth";
import { withIntl } from "@/lib/test-utils/with-intl";
import { AccountCard } from "../AccountCard";

const USER: CurrentUser = {
  id: "u-1",
  email: "anna.bauer@example.org",
  role: "admin",
  has_password: true,
  oidc_linked: false,
  ui_language: null,
};
const STATE: AuthState = {
  setup_required: false,
  oidc_enabled: true,
  oidc_button_label: "Keycloak",
  smtp_enabled: false,
  harness: false,
};

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;
let calls: { url: string; method: string; body: unknown }[];
const realFetch = globalThis.fetch;

function mockApi(handler: Handler) {
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    return handler(url, init);
  }) as unknown as typeof fetch;
}

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status });
const err = (status: number, code: string) => json(status, { detail: { error_code: code, message: "x" } });

function renderCard(user: Partial<CurrentUser> = {}, state: Partial<AuthState> = {}, extra: Record<string, unknown> = {}) {
  const value = { user: { ...USER, ...user }, authState: { ...STATE, ...state }, ...extra };
  return render(withIntl(
    <CurrentUserProvider value={value}>
      <AccountCard />
    </CurrentUserProvider>,
  ));
}

beforeEach(() => {
  window.history.replaceState({}, "", "/settings");
  sessionStorage.clear();
  mockApi(() => json(500));
});
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
  window.history.replaceState({}, "", "/");
  sessionStorage.clear();
});

async function openPasswordForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("account-password-open"));
}

async function fillPasswords(user: ReturnType<typeof userEvent.setup>, current: string, next: string, repeat: string) {
  await user.type(screen.getByTestId("account-password-current"), current);
  await user.type(screen.getByTestId("account-password-new"), next);
  await user.type(screen.getByTestId("account-password-repeat"), repeat);
  await user.click(screen.getByTestId("account-password-save"));
}

describe("AccountCard — identity", () => {
  it("shows the email and the role", () => {
    renderCard();
    expect(screen.getByTestId("account-email")).toHaveTextContent("anna.bauer@example.org");
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Admin");
  });

  it("shows 'User' for a non-admin", () => {
    renderCard({ role: "user" });
    expect(screen.getByTestId("role-badge")).toHaveTextContent("User");
  });
});

describe("AccountCard — password change", () => {
  it("too short: client-side error, no request", async () => {
    const user = userEvent.setup();
    renderCard();
    await openPasswordForm(user);
    await fillPasswords(user, "old-password-123", "short", "short");
    expect(screen.getByTestId("account-password-error")).toHaveTextContent("At least 12 characters.");
    expect(calls).toHaveLength(0);
  });

  it("mismatch: client-side error, no request", async () => {
    const user = userEvent.setup();
    renderCard();
    await openPasswordForm(user);
    await fillPasswords(user, "old-password-123", "a-long-enough-pass-1", "a-long-enough-pass-2");
    expect(screen.getByTestId("account-password-error")).toHaveTextContent("The two passwords do not match.");
    expect(calls).toHaveLength(0);
  });

  it("equal to the email (case-insensitive): client-side error, no request", async () => {
    const user = userEvent.setup();
    renderCard();
    await openPasswordForm(user);
    await fillPasswords(user, "old-password-123", "Anna.Bauer@Example.org", "Anna.Bauer@Example.org");
    expect(screen.getByTestId("account-password-error")).toHaveTextContent(
      "The password cannot be your email address.",
    );
    expect(calls).toHaveLength(0);
  });

  it("success (204): POST /api/auth/password {current,new} and the confirmation", async () => {
    mockApi(() => json(204));
    const user = userEvent.setup();
    renderCard();
    await openPasswordForm(user);
    await fillPasswords(user, "old-password-123", "a-long-enough-pass-1", "a-long-enough-pass-1");
    await waitFor(() => expect(screen.getByTestId("account-password-changed")).toBeInTheDocument());
    expect(screen.getByTestId("account-password-changed")).toHaveTextContent("Password changed.");
    expect(calls).toEqual([
      {
        url: "/api/auth/password",
        method: "POST",
        body: { current: "old-password-123", new: "a-long-enough-pass-1" },
      },
    ]);
    expect(screen.queryByTestId("account-password-form")).toBeNull();
  });

  it("403 invalid_credentials: 'The current password is incorrect.'", async () => {
    mockApi(() => err(403, "invalid_credentials"));
    const user = userEvent.setup();
    renderCard();
    await openPasswordForm(user);
    await fillPasswords(user, "wrong-password-1", "a-long-enough-pass-1", "a-long-enough-pass-1");
    await waitFor(() =>
      expect(screen.getByTestId("account-password-error")).toHaveTextContent("The current password is incorrect."),
    );
    // The form stays open so the person can retry.
    expect(screen.getByTestId("account-password-form")).toBeInTheDocument();
  });

  it("OIDC-only user: the no-password hint, no change button", () => {
    renderCard({ has_password: false, oidc_linked: true });
    expect(screen.getByTestId("account-password-row")).toHaveTextContent(
      "You sign in with Keycloak; this account has no password.",
    );
    expect(screen.queryByTestId("account-password-open")).toBeNull();
  });
});

describe("AccountCard — SSO row", () => {
  it("is hidden when OIDC is not enabled", () => {
    renderCard({}, { oidc_enabled: false });
    expect(screen.queryByTestId("account-sso-row")).toBeNull();
  });

  it("not linked: offers 'Link with Keycloak'", () => {
    renderCard();
    expect(screen.getByTestId("account-sso-link")).toHaveTextContent("Link with Keycloak");
    expect(screen.queryByTestId("account-sso-unlink")).toBeNull();
  });

  it("linked + password: offers Unlink", () => {
    renderCard({ oidc_linked: true });
    expect(screen.getByTestId("account-sso-unlink")).toHaveTextContent("Unlink");
  });

  it("linked + no password: no Unlink (the link is the only way in)", () => {
    renderCard({ oidc_linked: true, has_password: false });
    expect(screen.getByTestId("account-sso-row")).toBeInTheDocument();
    expect(screen.queryByTestId("account-sso-unlink")).toBeNull();
  });

  it("unlink 409 last_credential: the errorLastCredential copy", async () => {
    mockApi(() => err(409, "last_credential"));
    const user = userEvent.setup();
    renderCard({ oidc_linked: true });
    await user.click(screen.getByTestId("account-sso-unlink"));
    await waitFor(() =>
      expect(screen.getByTestId("account-sso-notice")).toHaveTextContent(
        "The link cannot be removed: this account has no password, so Keycloak is the only way in.",
      ),
    );
    expect(calls[0]).toMatchObject({ url: "/api/me/oidc", method: "DELETE" });
  });

  it("unlink 403 reauth_required: shows the continue prompt", async () => {
    mockApi(() => err(403, "reauth_required"));
    const user = userEvent.setup();
    renderCard({ oidc_linked: true });
    await user.click(screen.getByTestId("account-sso-unlink"));
    const prompt = await screen.findByTestId("account-sso-reauth");
    expect(prompt).toHaveTextContent("To confirm, you sign in with Keycloak once more.");
    expect(prompt).toHaveTextContent("Continue to Keycloak");
  });

  it("reauth marker for oidc.unlink: shows the confirmation next to Unlink", () => {
    window.history.replaceState({}, "", "/settings?reauth=oidc.unlink");
    renderCard({ oidc_linked: true });
    expect(screen.getByTestId("account-sso-confirmed")).toHaveTextContent("Confirmed with Keycloak. You can now unlink.");
  });
});

describe("AccountCard — delete account", () => {
  it("with a password: DELETE /api/me/account {password}", async () => {
    mockApi(() => json(500));
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByTestId("account-delete-open"));
    const confirm = screen.getByTestId("account-delete-confirm");
    expect(confirm).toBeDisabled();
    await user.type(screen.getByTestId("account-delete-password"), "my-password-123");
    await user.click(confirm);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/api/me/account", method: "DELETE", body: { password: "my-password-123" } });
  });

  it("wrong password (invalid_credentials): the wrong-password copy", async () => {
    mockApi(() => err(403, "invalid_credentials"));
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByTestId("account-delete-open"));
    await user.type(screen.getByTestId("account-delete-password"), "nope-nope-nope");
    await user.click(screen.getByTestId("account-delete-confirm"));
    expect(await screen.findByTestId("account-delete-error")).toHaveTextContent("The password is incorrect.");
  });

  it("409 last_admin: errorLastAdmin copy", async () => {
    mockApi(() => err(409, "last_admin"));
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByTestId("account-delete-open"));
    await user.type(screen.getByTestId("account-delete-password"), "my-password-123");
    await user.click(screen.getByTestId("account-delete-confirm"));
    expect(await screen.findByTestId("account-delete-last-admin")).toHaveTextContent(
      "You are the only admin of this instance. Make someone else an admin first",
    );
  });

  it("OIDC-only: 'Continue to Keycloak' posts /api/me/reauth/start {account.delete, user id}", async () => {
    // No authorize_url in the answer, so nothing navigates; the request is what we assert.
    mockApi(() => json(200, {}));
    const user = userEvent.setup();
    renderCard({ has_password: false, oidc_linked: true });
    await user.click(screen.getByTestId("account-delete-open"));
    expect(screen.queryByTestId("account-delete-password")).toBeNull();
    expect(screen.queryByTestId("account-delete-confirm")).toBeNull();
    const cont = screen.getByTestId("account-delete-continue-oidc");
    expect(cont).toHaveTextContent("Continue to Keycloak");
    await user.click(cont);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      url: "/api/me/reauth/start",
      method: "POST",
      body: { action: "account.delete", target_id: "u-1" },
    });
    expect(sessionStorage.getItem("applire.settings.oidcIntent")).toBe("account.delete");
  });
});

describe("AccountCard — IdP callback markers", () => {
  it("?reauth=account.delete for an OIDC-only user: dialog open, confirmed, delete button; marker stripped", () => {
    window.history.replaceState({}, "", "/settings?reauth=account.delete");
    renderCard({ has_password: false, oidc_linked: true });
    expect(screen.getByTestId("account-delete-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("account-delete-confirmed")).toHaveTextContent(
      "Confirmed with Keycloak. You can now delete your account.",
    );
    expect(screen.getByTestId("account-delete-confirm")).toBeEnabled();
    expect(screen.queryByTestId("account-delete-continue-oidc")).toBeNull();
    expect(window.location.search).toBe("");
  });

  it("the confirmed delete sends an empty body (no password)", async () => {
    mockApi(() => json(500));
    window.history.replaceState({}, "", "/settings?reauth=account.delete");
    const user = userEvent.setup();
    renderCard({ has_password: false, oidc_linked: true });
    await user.click(screen.getByTestId("account-delete-confirm"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/api/me/account", method: "DELETE", body: {} });
  });

  it("?oidc=linked: success notice, param stripped, user re-read", async () => {
    const refresh = vi.fn(async () => {});
    window.history.replaceState({}, "", "/settings?oidc=linked");
    renderCard({}, {}, { refresh });
    expect(screen.getByTestId("account-sso-notice")).toHaveTextContent("Your account is now linked with Keycloak.");
    expect(window.location.search).toBe("");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("?oidc=failed with intent 'link': errorOidcLinkFailed copy", () => {
    sessionStorage.setItem("applire.settings.oidcIntent", "link");
    window.history.replaceState({}, "", "/settings?oidc=failed");
    renderCard();
    expect(screen.getByTestId("account-sso-notice")).toHaveTextContent("Linking with Keycloak did not work. Try again.");
    // The intent is one-shot.
    expect(sessionStorage.getItem("applire.settings.oidcIntent")).toBeNull();
  });

  it("?oidc=failed with intent 'account.delete': delete dialog opens with the re-auth error", () => {
    sessionStorage.setItem("applire.settings.oidcIntent", "account.delete");
    window.history.replaceState({}, "", "/settings?oidc=failed");
    renderCard();
    expect(screen.getByTestId("account-delete-error")).toHaveTextContent(
      "Confirming with Keycloak did not work or has expired. Try again.",
    );
  });
});
