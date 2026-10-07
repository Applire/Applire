// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// US330 (Strawberry 2a) — the signed-out pages against stubbed auth endpoints:
// login (success → next, throttle, disabled, origin, share-target), setup,
// forgot, invite/reset (token only in the fragment + body, no-referrer).

import { test, expect, ADMIN_USER, UNAUTHENTICATED_BODY } from "../support/auth-fixture";

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) => ({
  status,
  contentType: "application/json",
  headers,
  body: JSON.stringify(body),
});
const err = (code: string, status: number, message = "x") => json({ detail: { error_code: code, message } }, status);

test.use({ authUser: null });

test.describe("/login", () => {
  test("204 → the person lands on ?next= with the session the server set", async ({ page }) => {
    let signedIn = false;
    let body: unknown = null;
    await page.route("**/api/auth/me", (r) =>
      signedIn ? r.fulfill(json(ADMIN_USER)) : r.fulfill(json(UNAUTHENTICATED_BODY, 401)),
    );
    await page.route("**/api/auth/login", (r) => {
      body = r.request().postDataJSON();
      signedIn = true;
      return r.fulfill({ status: 204 });
    });
    await page.route("**/api/profile", (r) => r.fulfill(json({ profile: null })));
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/login\?next=%2Fsettings$/);
    await page.waitForLoadState("networkidle");
    await page.getByLabel("Email address").fill(ADMIN_USER.email);
    await page.getByLabel("Password").fill("a long passphrase");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
    expect(body).toEqual({ email: ADMIN_USER.email, password: "a long passphrase" });
  });

  test("MD-20: a delayed 401 carrying X-Applire-Throttled shows the throttle notice", async ({ page }) => {
    await page.route("**/api/auth/login", (r) =>
      r.fulfill(json({ detail: { error_code: "invalid_credentials", message: "x" } }, 401, { "X-Applire-Throttled": "1" })),
    );
    await page.goto("/login");
    await page.getByLabel("Email address").fill("jonas.keller@example.org");
    await page.getByLabel("Password").fill("wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Several failed attempts for this address.")).toBeVisible();
    await expect(page).toHaveURL(/\/login$/); // a login 401 never triggers the global redirect
  });

  test("W0B-3 disabled account, origin mismatch (server text verbatim)", async ({ page }) => {
    let answer = err("account_disabled", 403);
    await page.route("**/api/auth/login", (r) => r.fulfill(answer));
    await page.goto("/login");
    await page.getByLabel("Email address").fill("lukas.brandt@example.org");
    await page.getByLabel("Password").fill("correct one");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("This account is disabled.")).toBeVisible();
    const serverText = "Origin http://192.168.1.20:8080 does not match Host applire:80. Your reverse proxy must forward the Host header, or set APPLIRE_BASE_URL to the address you open.";
    answer = err("origin_mismatch", 403, serverText);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Sign-in refused from this address")).toBeVisible();
    await expect(page.getByText(serverText)).toBeVisible();
  });

  test("ruling w4-2a-1: share while signed out → /login with NO notice → sign-in lands on the prefilled dashboard", async ({ page }) => {
    let signedIn = false;
    await page.route("**/api/auth/me", (r) =>
      signedIn ? r.fulfill(json(ADMIN_USER)) : r.fulfill(json(UNAUTHENTICATED_BODY, 401)),
    );
    await page.route("**/api/auth/login", (r) => {
      signedIn = true;
      return r.fulfill({ status: 204 });
    });
    await page.route("**/api/profile", (r) => r.fulfill(json({ profile: null })));
    await page.route("**/api/applications**", (r) => r.fulfill(json([])));
    await page.goto("/share-target?text=Senior%20QA%20https%3A%2F%2Fjobs.example.org%2F42");
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard%3Fjd_url%3Dhttps/);
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByText("You shared something")).toHaveCount(0);
    await expect(page.locator('[data-testid^="auth-notice"], [data-testid="auth-error"]')).toHaveCount(0);
    await page.getByLabel("Email address").fill(ADMIN_USER.email);
    await page.getByLabel("Password").fill("a long passphrase");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard\?jd_url=https/);
    await expect(page.getByTestId("quick-tailor-url-input")).toHaveValue("https://jobs.example.org/42");
  });

  test.describe("OIDC configured", () => {
    test.use({ authState: { oidc_enabled: true, oidc_button_label: "Company login" } });
    test("labelled SSO button; ?error=oidc_no_account worded", async ({ page }) => {
      await page.goto("/login?error=oidc_no_account");
      await expect(page.getByRole("button", { name: "Sign in with Company login" })).toBeVisible();
      await expect(page.getByText("There is no account here for this sign-in.")).toBeVisible();
    });
  });
});

test.describe("/setup", () => {
  test.use({ authState: { setup_required: true } });

  test("wrong code, then the right one → signed in", async ({ page }) => {
    let calls = 0;
    await page.route("**/api/setup", (r) => {
      calls += 1;
      return calls === 1 ? r.fulfill(err("invalid_setup_token", 403)) : r.fulfill({ status: 204 });
    });
    await page.route("**/api/profile/exists", (r) => r.fulfill(json({ exists: false })));
    await page.goto("/setup");
    await expect(page.getByText("docker compose logs backend")).toBeVisible();
    await page.getByLabel("Setup code").fill("AAAA-BBBB");
    await page.getByLabel("Email address").fill(ADMIN_USER.email);
    await page.getByLabel("Password", { exact: true }).fill("a long passphrase");
    await page.getByLabel("Repeat password").fill("a long passphrase");
    await page.getByRole("button", { name: "Set up and sign in" }).click();
    await expect(page.getByText("This code does not match.")).toBeVisible();
    await page.getByLabel("Setup code").fill("CCCC-DDDD");
    await page.getByRole("button", { name: "Set up and sign in" }).click();
    await expect.poll(() => new URL(page.url()).pathname).not.toBe("/setup");
  });
});

test.describe("/forgot", () => {
  test.use({ authState: { smtp_enabled: true } });
  test("same answer for every address", async ({ page }) => {
    await page.route("**/api/auth/forgot", (r) => r.fulfill({ status: 202 }));
    await page.goto("/forgot");
    await page.getByLabel("Email address").fill("nobody@example.org");
    await page.getByRole("button", { name: "Send link" }).click();
    await expect(page.getByText("If there is an account for nobody@example.org, a link is on its way.")).toBeVisible();
  });
});

test.describe("/invite and /reset", () => {
  test("invite: token from the fragment, sent only in POST bodies; no-referrer; redeem signs in", async ({ page }) => {
    const seen: { url: string; body: string | null }[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/")) seen.push({ url: req.url(), body: req.postData() });
    });
    await page.route("**/api/auth/links/inspect", (r) =>
      r.fulfill(json({ purpose: "invite", email: "mira.santos@example.org", state: "valid" })),
    );
    await page.route("**/api/auth/links/redeem", (r) => r.fulfill({ status: 204 }));
    await page.route("**/api/profile/exists", (r) => r.fulfill(json({ exists: false })));
    await page.goto("/invite#SECRETtoken123");
    await expect(page.getByText("Set a password for mira.santos@example.org")).toBeVisible();
    await expect(page.locator('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");
    await page.getByLabel("Password", { exact: true }).fill("a long passphrase");
    await page.getByLabel("Repeat password").fill("a long passphrase");
    await page.getByRole("button", { name: "Set password and get started" }).click();
    await expect.poll(() => new URL(page.url()).pathname).not.toBe("/invite");
    expect(seen.filter((s) => s.url.includes("SECRETtoken123"))).toEqual([]);
    expect(seen.filter((s) => (s.body ?? "").includes("SECRETtoken123")).length).toBe(2);
  });

  test("reset: expired (no SMTP) points to the operator; used; garbled", async ({ page }) => {
    let answer = json({ purpose: "reset", email: "jonas.keller@example.org", state: "expired" });
    await page.route("**/api/auth/links/inspect", (r) => r.fulfill(answer));
    await page.goto("/reset#t1");
    await expect(page.getByText("This link has expired")).toBeVisible();
    await expect(page.getByText("Reset links are valid for one hour. Ask the person who runs this Applire instance for a new one.")).toBeVisible();
    answer = json({ purpose: "reset", email: "jonas.keller@example.org", state: "used" });
    await page.goto("/reset#t2");
    await page.reload();
    await expect(page.getByText("This link has already been used")).toBeVisible();
    answer = err("link_invalid", 404);
    await page.goto("/reset#t3");
    await page.reload();
    await expect(page.getByText("This link does not work")).toBeVisible();
  });
});
