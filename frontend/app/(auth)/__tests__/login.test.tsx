// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import { CurrentUserProvider, type CurrentUserValue } from "@/lib/auth/current-user";
import { withIntl } from "@/lib/test-utils/with-intl";

import LoginPage from "../login/page";

const replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search),
}));

const STATE = { setup_required: false, oidc_enabled: false, oidc_button_label: "Firmen-Login", smtp_enabled: true, harness: false };

const realFetch = globalThis.fetch;
const realLocation = window.location;
let assign: ReturnType<typeof vi.fn>;

function renderLogin(auth: Partial<CurrentUserValue> = { status: "unauthenticated", authState: STATE }) {
  return render(withIntl(<CurrentUserProvider value={auth}><LoginPage /></CurrentUserProvider>, "de"));
}

function respond(status: number, body: unknown = null, headers: Record<string, string> = {}) {
  const fn = vi.fn(async () => new Response(body === null ? null : JSON.stringify(body), { status, headers }));
  globalThis.fetch = fn as unknown as typeof fetch;
  return fn;
}

async function signIn(email = "anna.bauer@example.org", password = "a long passphrase") {
  fireEvent.change(screen.getByLabelText(de.auth.emailLabel), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(de.auth.passwordLabel), { target: { value: password } });
  fireEvent.click(screen.getByTestId("login-submit"));
}

beforeEach(() => {
  search = "";
  replace.mockReset();
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...realLocation, pathname: "/login", search: "", assign },
  });
});
afterEach(() => {
  globalThis.fetch = realFetch;
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

describe("/login (US330)", () => {
  it("renders the approved copy: title, subtitle, forgot link, no-account hint", () => {
    renderLogin();
    expect(screen.getByRole("heading", { name: de.auth.loginTitle })).toBeInTheDocument();
    expect(screen.getByText(de.auth.loginSubtitle)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: de.auth.forgotLink })).toHaveAttribute("href", "/forgot");
    expect(screen.getByText(de.auth.noAccountHint)).toBeInTheDocument();
    expect(screen.queryByTestId("login-oidc")).toBeNull();
  });

  it("204 → full navigation to the guarded ?next=", async () => {
    search = "next=%2Fflow%2F6f1c%2Fcv";
    const fetchMock = respond(204);
    renderLogin();
    await signIn();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/flow/6f1c/cv"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/auth\/login$/);
    expect(JSON.parse(String(init.body))).toEqual({ email: "anna.bauer@example.org", password: "a long passphrase" });
  });

  it("an off-site ?next= is never followed", async () => {
    search = "next=%2F%2Fevil.example%2F";
    respond(204);
    renderLogin();
    await signIn();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  });

  it("401 invalid_credentials → the generic error", async () => {
    respond(401, { detail: { error_code: "invalid_credentials", message: "x" } });
    renderLogin();
    await signIn();
    expect(await screen.findByText(de.auth.errorInvalidCredentials)).toBeInTheDocument();
  });

  it("MD-20: a delayed 401 with X-Applire-Throttled: 1 → the throttle notice instead", async () => {
    respond(401, { detail: { error_code: "invalid_credentials", message: "x" } }, { "X-Applire-Throttled": "1" });
    renderLogin();
    await signIn();
    expect(await screen.findByText(de.auth.errorThrottled)).toBeInTheDocument();
    expect(screen.queryByText(de.auth.errorInvalidCredentials)).toBeNull();
  });

  it("W0B-3: 403 account_disabled → the disabled text", async () => {
    respond(403, { detail: { error_code: "account_disabled", message: "x" } });
    renderLogin();
    await signIn();
    expect(await screen.findByText(de.auth.errorAccountDisabled)).toBeInTheDocument();
  });

  it("403 origin_mismatch → title, hint and the server's message verbatim", async () => {
    const msg = "Origin http://192.168.1.20:8080 does not match Host applire:80. Your reverse proxy must forward the Host header, or set APPLIRE_BASE_URL to the address you open.";
    respond(403, { detail: { error_code: "origin_mismatch", message: msg } });
    renderLogin();
    await signIn();
    expect(await screen.findByText(de.auth.errorOriginTitle)).toBeInTheDocument();
    expect(screen.getByText(msg)).toBeInTheDocument();
  });

  it("network failure → 'cannot be reached'", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    renderLogin();
    await signIn();
    expect(await screen.findByText(de.auth.errorNetwork)).toBeInTheDocument();
  });

  it("?expired=1 → session-ended notice, no subtitle (redirect-401 mock)", () => {
    search = "next=%2Fflow%2F1%2Fcv&expired=1";
    renderLogin();
    expect(screen.getByText(de.auth.sessionExpired)).toBeInTheDocument();
    expect(screen.queryByText(de.auth.loginSubtitle)).toBeNull();
  });

  it("ruling w4-2a-1: a share deep link in ?next= shows NO notice, and sign-in lands on the prefilled dashboard", async () => {
    const next = "/dashboard?jd_url=" + encodeURIComponent("https://jobs.example/1");
    search = "next=" + encodeURIComponent(next);
    respond(204);
    renderLogin();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText(de.auth.loginSubtitle)).toBeInTheDocument();
    await signIn();
    await waitFor(() => expect(assign).toHaveBeenCalledWith(next));
  });

  it("?signed_out=1 → signed-out notice", () => {
    search = "signed_out=1";
    renderLogin();
    expect(screen.getByText(de.auth.signedOut)).toBeInTheDocument();
  });

  it("OIDC on: the labelled button + divider; callback errors are worded", () => {
    search = "error=oidc_failed";
    renderLogin({ status: "unauthenticated", authState: { ...STATE, oidc_enabled: true } });
    expect(screen.getByTestId("login-oidc").textContent).toContain("Mit Firmen-Login anmelden");
    expect(screen.getByText(de.auth.orDivider)).toBeInTheDocument();
    expect(screen.getByText(de.auth.errorOidcFailed.replace("{label}", "Firmen-Login"))).toBeInTheDocument();
  });

  it("?oidc=failed is the same as error=oidc_failed; oidc_no_account has its own text", () => {
    search = "oidc=failed";
    const { unmount } = renderLogin({ status: "unauthenticated", authState: { ...STATE, oidc_enabled: true } });
    expect(screen.getByText(de.auth.errorOidcFailed.replace("{label}", "Firmen-Login"))).toBeInTheDocument();
    unmount();
    search = "error=oidc_no_account";
    renderLogin();
    expect(screen.getByText(de.auth.errorOidcNoAccount)).toBeInTheDocument();
  });

  it("first run → /setup; already signed in → straight to next", () => {
    renderLogin({ status: "unauthenticated", authState: { ...STATE, setup_required: true } });
    expect(replace).toHaveBeenCalledWith("/setup");
    replace.mockReset();
    search = "next=%2Fdocuments";
    renderLogin({
      status: "authenticated",
      authState: STATE,
      user: { id: "u", email: "a@b.c", role: "user", has_password: true, oidc_linked: false, ui_language: "de" },
    });
    expect(replace).toHaveBeenCalledWith("/documents");
  });
});
