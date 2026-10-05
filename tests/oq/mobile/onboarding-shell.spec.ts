// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Strawberry int-1 at 390x844: a profileless invited user on "/" has the
// account menu (sign-out) and the import entry, without page-level overflow.

import { test, expect, REGULAR_USER } from "../../support/auth-fixture";

const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

test.use({ authUser: REGULAR_USER });

test("profileless user on / at 390 px: account menu with sign-out, import entry, no horizontal overflow", async ({ page }) => {
  await page.route("**/api/profile/exists", (r) => r.fulfill(json({ exists: false })));
  await page.route("**/api/profile", (r) => r.fulfill(json({ detail: "No profile" }, 404)));
  await page.goto("/");
  await expect(page.getByTestId("onboarding-in-shell")).toBeVisible();
  await expect(page.getByTestId("submit-button")).toBeVisible();
  await page.getByTestId("user-menu-trigger").click();
  await expect(page.getByTestId("user-menu-sign-out")).toBeVisible();
  await expect(page.getByTestId("user-menu-admin")).toHaveCount(0);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
});
