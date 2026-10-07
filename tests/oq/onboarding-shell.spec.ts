// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Strawberry int-1 (approved mock user-menu screen 3 "Neue Person, noch kein
// Profil"): a signed-in account without a profile lands on "/" — the CV import
// / onboarding — INSIDE the shell: e-mail in the sidebar, account menu with
// sign-out, Administration only for admins. The harness first run keeps its
// standalone page. Every /api/* call is stubbed.

import { test, expect, ADMIN_USER, REGULAR_USER } from "../support/auth-fixture";

const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

test.beforeEach(async ({ page }) => {
  await page.route("**/api/profile/exists", (r) => r.fulfill(json({ exists: false })));
  await page.route("**/api/profile", (r) => r.fulfill(json({ detail: "No profile" }, 404)));
});

test("admin after setup (no profile): onboarding in the shell, Administration reachable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("onboarding-in-shell")).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  // The import entry stays on this page (v0.42 onboarding unchanged).
  await expect(page.getByTestId("submit-button")).toBeVisible();
  await expect(page.getByTestId("no-cv-button")).toBeVisible();
  const sidebar = page.getByTestId("app-sidebar");
  await expect(sidebar.getByTestId("sidebar-user-email")).toHaveText(ADMIN_USER.email);
  await sidebar.getByTestId("sidebar-nav-admin").click();
  await expect(page).toHaveURL(/\/admin\/overview$/);
});

test.describe("invited user after redeem (no profile)", () => {
  test.use({ authUser: REGULAR_USER });

  test("account menu with sign-out, no Administration", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("onboarding-in-shell")).toBeVisible();
    await expect(page.getByTestId("submit-button")).toBeVisible();
    await expect(page.getByTestId("sidebar-nav-admin")).toHaveCount(0);
    await page.getByTestId("user-menu-trigger").click();
    await expect(page.getByTestId("user-menu-sign-out")).toBeVisible();
    await expect(page.getByTestId("user-menu-admin")).toHaveCount(0);
  });
});

test.describe("harness first run", () => {
  test.use({ authState: { harness: true } });

  test("keeps the standalone onboarding page (no shell)", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("submit-button")).toBeVisible();
    await expect(page.getByTestId("onboarding-in-shell")).toHaveCount(0);
    await expect(page.getByTestId("harness-banner")).toBeVisible();
  });
});
