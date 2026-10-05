// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// US330 (Strawberry 2a) — the signed-in shell around the account: gate, account
// menu, Administration nav (admin only), sign-out, harness banner, the global
// 401 handler. Every /api/* call is stubbed; runs on any backend (or none,
// with E2E_STUBBED_ONLY=1).

import { test, expect, ADMIN_USER, REGULAR_USER, UNAUTHENTICATED_BODY } from "../support/auth-fixture";

const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

test.describe("signed-in shell (US330)", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/profile", (r) => r.fulfill(json({ profile: { personal_info: { name: "Anna Bauer" } } })));
  });

  test("admin: e-mail under the name, Administration nav → /admin/users, account menu", async ({ page }) => {
    await page.goto("/settings");
    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar.getByTestId("sidebar-user-email")).toHaveText(ADMIN_USER.email);
    await expect(sidebar.getByText("Anna Bauer")).toBeVisible();
    await page.getByTestId("user-menu-trigger").click();
    const menu = page.getByTestId("user-menu");
    await expect(menu.getByText("Signed in as")).toBeVisible();
    await expect(menu.getByTestId("user-menu-role")).toHaveText("Admin");
    await expect(menu.getByRole("menuitem", { name: "Administration" })).toBeVisible();
    await page.keyboard.press("Escape");
    await sidebar.getByTestId("sidebar-nav-admin").click();
    await expect(page).toHaveURL(/\/admin\/users$/);
  });

  test.describe("plain user", () => {
    test.use({ authUser: REGULAR_USER });

    test("no Administration anywhere; role chip 'User'", async ({ page }) => {
      await page.goto("/settings");
      await expect(page.getByTestId("app-sidebar")).toBeVisible();
      await expect(page.getByTestId("sidebar-nav-admin")).toHaveCount(0);
      await page.getByTestId("user-menu-trigger").click();
      await expect(page.getByTestId("user-menu-role")).toHaveText("User");
      await expect(page.getByTestId("user-menu-admin")).toHaveCount(0);
    });
  });

  test("sign out → POST /api/auth/logout, applire.* browser state cleared, /login?signed_out=1", async ({ page }) => {
    let logoutCalls = 0;
    await page.route("**/api/auth/logout", (r) => {
      logoutCalls += 1;
      return r.fulfill({ status: 204 });
    });
    await page.goto("/settings");
    await page.evaluate(() => {
      localStorage.setItem("applire.gaps.decisionsDismissed.f1.u.x", "[\"a\"]");
      sessionStorage.setItem("applire.finetune.saveScope.u.x", "cv");
      localStorage.setItem("other.key", "kept");
    });
    await page.getByTestId("user-menu-trigger").click();
    await page.getByTestId("user-menu-sign-out").click();
    await expect(page).toHaveURL(/\/login\?signed_out=1$/);
    expect(logoutCalls).toBe(1);
    const left = await page.evaluate(() => [
      localStorage.getItem("applire.gaps.decisionsDismissed.f1.u.x"),
      sessionStorage.getItem("applire.finetune.saveScope.u.x"),
      localStorage.getItem("other.key"),
    ]);
    expect(left).toEqual([null, null, "kept"]);
  });

  test.describe("harness", () => {
    test.use({ authState: { harness: true } });

    test("red test-mode banner on shell pages", async ({ page }) => {
      await page.goto("/settings");
      await expect(page.getByTestId("harness-banner")).toContainText("TEST MODE");
    });
  });
});

test.describe("signed out (US330)", () => {
  test.use({ authUser: null });

  test("a shell URL redirects to /login?next=<path+query>", async ({ page }) => {
    await page.goto("/documents?tab=letters");
    await expect(page).toHaveURL(/\/login\?next=%2Fdocuments%3Ftab%3Dletters$/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("the start page redirects to /login (no next for /)", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
  });

  test.describe("unclaimed instance", () => {
    test.use({ authState: { setup_required: true } });
    test("a shell URL goes to /setup", async ({ page }) => {
      await page.goto("/dashboard");
      await expect(page).toHaveURL(/\/setup$/);
      await expect(page.getByRole("heading", { name: "Set up Applire" })).toBeVisible();
    });
  });

  test.describe("harness", () => {
    test.use({ authState: { harness: true } });
    test("the banner shows on signed-out pages too", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByTestId("harness-banner")).toBeVisible();
    });
  });
});

test("session ends mid-visit: the next API 401 sends you to /login?next=…&expired=1", async ({ page }) => {
  let signedIn = true;
  await page.route("**/api/auth/me", (r) =>
    signedIn ? r.fulfill(json(ADMIN_USER)) : r.fulfill(json(UNAUTHENTICATED_BODY, 401)),
  );
  await page.route("**/api/profile", (r) =>
    signedIn ? r.fulfill(json({ profile: null })) : r.fulfill(json(UNAUTHENTICATED_BODY, 401)),
  );
  await page.goto("/settings");
  await expect(page.getByTestId("app-sidebar")).toBeVisible();
  signedIn = false;
  await page.evaluate(() => fetch("/api/profile"));
  await expect(page).toHaveURL(/\/login\?next=%2Fsettings&expired=1$/);
  await expect(page.getByText("Your session has ended.")).toBeVisible();
});

test("a 401 with another code (wrong current password) never redirects", async ({ page }) => {
  await page.route("**/api/profile", (r) => r.fulfill(json({ profile: null })));
  await page.route("**/api/x-probe", (r) =>
    r.fulfill(json({ detail: { error_code: "invalid_credentials", message: "x" } }, 401)),
  );
  await page.goto("/settings");
  await expect(page.getByTestId("app-sidebar")).toBeVisible();
  await page.evaluate(() => fetch("/api/x-probe"));
  await page.waitForTimeout(500);
  await expect(page).toHaveURL(/\/settings$/);
});
