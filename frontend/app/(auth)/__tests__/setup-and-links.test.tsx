// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import { CurrentUserProvider, type CurrentUserValue } from "@/lib/auth/current-user";
import { withIntl } from "@/lib/test-utils/with-intl";

import ForgotPage from "../forgot/page";
import SetupPage from "../setup/page";
import { SetPasswordPage, tokenFromHash } from "../_components/set-password";
import { passwordProblems } from "../_components/auth-ui";

const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push }),
  useSearchParams: () => new URLSearchParams(""),
}));

const STATE = { setup_required: true, oidc_enabled: false, oidc_button_label: "Firmen-Login", smtp_enabled: true, harness: false };

const realFetch = globalThis.fetch;
const realLocation = window.location;
let assign: ReturnType<typeof vi.fn>;

function wrap(node: React.ReactNode, auth: Partial<CurrentUserValue>) {
  return render(withIntl(<CurrentUserProvider value={auth}>{node}</CurrentUserProvider>, "de"));
}

type Route = (url: string, init?: RequestInit) => Response;
function routes(handler: Route) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => handler(url, init));
  globalThis.fetch = fn as unknown as typeof fetch;
  return fn;
}
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  push.mockReset();
  replace.mockReset();
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...realLocation, pathname: "/setup", search: "", hash: "", assign },
  });
});
afterEach(() => {
  globalThis.fetch = realFetch;
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("passwordProblems — contract §1 rules", () => {
  it("12–256, repeat must match, never the e-mail", () => {
    expect(passwordProblems("short", "short", "a@b.c")).toEqual(["tooShort"]);
    expect(passwordProblems("x".repeat(257), "x".repeat(257), "a@b.c")).toEqual(["tooLong"]);
    expect(passwordProblems("a long passphrase", "a long passphrasf", "a@b.c")).toEqual(["mismatch"]);
    expect(passwordProblems("Anna.Bauer@example.org", "Anna.Bauer@example.org", "anna.bauer@example.org")).toEqual(["isEmail"]);
    expect(passwordProblems("a long passphrase", "a long passphrase", "a@b.c")).toEqual([]);
  });
});

describe("/setup (US330, S-14)", () => {
  const auth = { status: "unauthenticated" as const, authState: STATE };

  it("fresh: intro, upgrade note (W0B-2), code help with both commands", () => {
    wrap(<SetupPage />, auth);
    expect(screen.getByRole("heading", { name: de.auth.setupTitle })).toBeInTheDocument();
    expect(screen.getByText(de.auth.setupUpgradeNote)).toBeInTheDocument();
    expect(screen.getByText("docker compose logs backend")).toBeInTheDocument();
    expect(
      screen.getByText("docker compose exec backend python -m applire.admin create-admin --email du@example.org"),
    ).toBeInTheDocument();
  });

  it("client checks: too short + mismatch shown together, nothing posted", () => {
    const fetchMock = routes(() => json(204, {}));
    wrap(<SetupPage />, auth);
    fill(de.auth.setupCodeLabel, "K7QF-2MXA");
    fill(de.auth.emailLabel, "anna.bauer@example.org");
    fill(de.auth.setupPasswordLabel, "kurz");
    fill(de.auth.passwordRepeatLabel, "anders");
    fireEvent.click(screen.getByTestId("setup-submit"));
    expect(screen.getByText(`${de.auth.errorPasswordTooShort} ${de.auth.errorPasswordMismatch}`)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  async function submitValid() {
    fill(de.auth.setupCodeLabel, "K7QF-2MXA-0000");
    fill(de.auth.emailLabel, "anna.bauer@example.org");
    fill(de.auth.setupPasswordLabel, "a long passphrase");
    fill(de.auth.passwordRepeatLabel, "a long passphrase");
    fireEvent.click(screen.getByTestId("setup-submit"));
  }

  it("204 → signed in, to /", async () => {
    const fetchMock = routes(() => new Response(null, { status: 204 }));
    wrap(<SetupPage />, auth);
    await submitValid();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      setup_token: "K7QF-2MXA-0000",
      email: "anna.bauer@example.org",
      password: "a long passphrase",
    });
  });

  it("403 invalid_setup_token → the rotating-code error under the field", async () => {
    routes(() => json(403, { detail: { error_code: "invalid_setup_token", message: "x" } }));
    wrap(<SetupPage />, auth);
    await submitValid();
    expect(await screen.findByText(de.auth.errorSetupCode)).toBeInTheDocument();
  });

  it("409 setup_done → already-set-up card with the way to sign-in", async () => {
    routes(() => json(409, { detail: { error_code: "setup_done", message: "x" } }));
    wrap(<SetupPage />, auth);
    await submitValid();
    expect(await screen.findByText(de.auth.errorSetupDone)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: de.auth.goToLogin }));
    expect(push).toHaveBeenCalledWith("/login");
  });

  it("harness → setup switched off (no form)", () => {
    wrap(<SetupPage />, { status: "error", authState: { ...STATE, harness: true } });
    expect(screen.getByText(de.auth.errorHarnessActive)).toBeInTheDocument();
    expect(screen.queryByTestId("setup-submit")).toBeNull();
  });

  it("claimed instance → already-set-up card straight away", () => {
    wrap(<SetupPage />, { status: "unauthenticated", authState: { ...STATE, setup_required: false } });
    expect(screen.getByText(de.auth.errorSetupDone)).toBeInTheDocument();
  });
});

describe("/forgot (S-7)", () => {
  it("SMTP on: always the same 'if there is an account' answer", async () => {
    routes(() => new Response(null, { status: 202 }));
    wrap(<ForgotPage />, { status: "unauthenticated", authState: STATE });
    fill(de.auth.emailLabel, "jonas.keller@example.org");
    fireEvent.click(screen.getByTestId("forgot-submit"));
    expect(await screen.findByText(de.auth.forgotSent.replace("{email}", "jonas.keller@example.org"))).toBeInTheDocument();
  });

  it("SMTP off: who to ask + the CLI reset, no form", () => {
    wrap(<ForgotPage />, { status: "unauthenticated", authState: { ...STATE, smtp_enabled: false } });
    expect(screen.getByText(de.auth.forgotNoMailIntro)).toBeInTheDocument();
    expect(
      screen.getByText("docker compose exec backend python -m applire.admin reset-password --email du@example.org"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("forgot-submit")).toBeNull();
  });
});

describe("/invite + /reset (ADR-091 cl. 22)", () => {
  function at(hash: string) {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...realLocation, pathname: "/invite", search: "", hash, assign },
    });
  }
  const auth = { status: "unauthenticated" as const, authState: { ...STATE, setup_required: false } };

  it("the token comes from the fragment only", () => {
    expect(tokenFromHash("#abc_DEF-123")).toBe("abc_DEF-123");
    expect(tokenFromHash("")).toBe("");
  });

  it("no token → 'does not work', nothing posted", async () => {
    at("");
    const fetchMock = routes(() => json(200, {}));
    wrap(<SetPasswordPage purpose="invite" />, auth);
    expect(await screen.findByText(de.auth.linkInvalidTitle)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("valid invite → form for the e-mail → redeem posts token in the BODY → signed in", async () => {
    at("#tok123");
    const fetchMock = routes((url) =>
      url.endsWith("/inspect")
        ? json(200, { purpose: "invite", email: "mira.santos@example.org", state: "valid" })
        : new Response(null, { status: 204 }),
    );
    wrap(<SetPasswordPage purpose="invite" />, auth);
    expect(await screen.findByText(de.auth.inviteIntro.replace("{email}", "mira.santos@example.org"))).toBeInTheDocument();
    fill(de.auth.passwordLabel, "a long passphrase");
    fill(de.auth.passwordRepeatLabel, "a long passphrase");
    fireEvent.click(screen.getByTestId("link-submit"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    for (const [url, init] of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
      expect(url).not.toContain("tok123");
      expect(String(init.body)).toContain("tok123");
    }
  });

  it.each([
    ["expired", "reset", true, de.auth.resetExpiredBody, de.auth.requestNewLink],
    ["expired", "reset", false, de.auth.resetExpiredBodyNoMail, null],
    ["expired", "invite", true, de.auth.inviteExpiredBody, null],
  ] as const)("%s %s (smtp=%s) → the matching expired text", async (state, purpose, smtp, body, extra) => {
    at("#tok");
    routes(() => json(200, { purpose, email: "x@example.org", state }));
    wrap(<SetPasswordPage purpose={purpose} />, { ...auth, authState: { ...auth.authState, smtp_enabled: smtp } });
    expect(await screen.findByText(body)).toBeInTheDocument();
    expect(screen.getByText(de.auth.linkExpiredTitle)).toBeInTheDocument();
    if (extra) expect(screen.getByRole("button", { name: extra })).toBeInTheDocument();
    else expect(screen.queryByRole("button", { name: de.auth.requestNewLink })).toBeNull();
  });

  it("used → 'already used'; 404 → 'does not work'", async () => {
    at("#tok");
    routes(() => json(200, { purpose: "reset", email: "x@example.org", state: "used" }));
    const { unmount } = wrap(<SetPasswordPage purpose="reset" />, auth);
    expect(await screen.findByText(de.auth.linkUsedTitle)).toBeInTheDocument();
    unmount();
    routes(() => json(404, { detail: { error_code: "link_invalid", message: "x" } }));
    wrap(<SetPasswordPage purpose="reset" />, auth);
    expect(await screen.findByText(de.auth.linkInvalidTitle)).toBeInTheDocument();
  });

  it("redeem 409 link_used (raced) → 'already used'", async () => {
    at("#tok");
    routes((url) =>
      url.endsWith("/inspect")
        ? json(200, { purpose: "reset", email: "jonas.keller@example.org", state: "valid" })
        : json(409, { detail: { error_code: "link_used", message: "x" } }),
    );
    wrap(<SetPasswordPage purpose="reset" />, auth);
    expect(await screen.findByText(de.auth.resetIntro.replace("{email}", "jonas.keller@example.org"))).toBeInTheDocument();
    fill(de.auth.passwordLabel, "a long passphrase");
    fill(de.auth.passwordRepeatLabel, "a long passphrase");
    fireEvent.click(screen.getByTestId("link-submit"));
    expect(await screen.findByText(de.auth.linkUsedTitle)).toBeInTheDocument();
  });
});
