// tests/oq/mobile/dashboard-shell.spec.ts

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

import { test, expect } from "@playwright/test";

/**
 * Mobile shell + dashboard capture — OQ Tests (US227, 390x844)
 *
 * Covers:
 *  - Dashboard "capture" surface (US224): single-column applications grid,
 *    no horizontal body overflow.
 *  - Shell chrome (US223): the hamburger opens MobileNavDrawer and the drawer
 *    navigates (and closes) — the primary nav path when the persistent
 *    AppSidebar is hidden below `md`.
 *
 * Uses page.route() mocks — does NOT require a real backend response, only
 * the mock-provider Docker stack serving the branch's frontend build.
 */

const MOCK_APPLICATIONS = {
  items: [
    {
      id: "app-mobile-0000-0000-0000-000000000001",
      role_title: "Senior Frontend Engineer",
      company_name: "Beispiel GmbH",
      workflow_status: "cv_ready",
      user_status: "tracking",
      flow_session_id: "flow-mobile-0000-0000-0000-000000000001",
      updated_at: new Date().toISOString(),
    },
    {
      id: "app-mobile-0000-0000-0000-000000000002",
      role_title: "Platform Engineer",
      company_name: "Zweite AG",
      workflow_status: "analyzing",
      user_status: "tracking",
      flow_session_id: "flow-mobile-0000-0000-0000-000000000002",
      updated_at: new Date().toISOString(),
    },
  ],
};

const MOCK_PROFILE = {
  profile: { personal_info: { name: "Emma Musterfrau" } },
  stats: { positions: 3, projects: 2, certifications: 1, data_points: 20 },
};

async function setupDashboardMocks(page: import("@playwright/test").Page) {
  await page.route("**/api/applications", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_APPLICATIONS) })
  );
  await page.route("**/api/profile/exists", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ exists: true, completeness_score: 0.8 }) })
  );
  await page.route("**/api/profile/import-jobs?active=true", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) })
  );
  // /api/profile is hit by several widgets on the dashboard; keep one shared fixture.
  await page.route("**/api/profile", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
  );
  // Destination pages reached via the drawer (profile) fetch these — keep the
  // navigation assertion from failing on an unrelated unmocked-route crash.
  await page.route("**/api/profile/enrichment-history", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) })
  );
  await page.route("**/api/profile/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 0.8, checks: [] }) })
  );
}

test.describe("Mobile dashboard + shell (390x844)", () => {
  test.beforeEach(async ({ page }) => {
    await setupDashboardMocks(page);
  });

  test("applications grid is single-column and the shell has no horizontal overflow", async ({ page }, testInfo) => {
    await page.goto("/dashboard");

    const grid = page.getByTestId("applications-grid");
    await expect(grid).toBeVisible({ timeout: 10000 });

    // Single column below md: grid-cols-1 md:grid-cols-2 — assert the two
    // application cards stack vertically (second card starts below the first,
    // not beside it).
    const cards = grid.locator(":scope > *");
    await expect(cards).toHaveCount(2);
    const firstBox = await cards.nth(0).boundingBox();
    const secondBox = await cards.nth(1).boundingBox();
    expect(firstBox).not.toBeNull();
    expect(secondBox).not.toBeNull();
    expect(secondBox!.y).toBeGreaterThanOrEqual(firstBox!.y + firstBox!.height - 1);

    // No horizontal body overflow at 390px — the verification-hierarchy check
    // an a11y snapshot cannot make (US227 AC).
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.body.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);

    // Evidence artifact (verification-hierarchy: real screenshot, not the a11y
    // snapshot) — written to test-results/ AND embedded in the HTML report.
    await page.screenshot({ path: testInfo.outputPath("dashboard-mobile-390x844.png"), fullPage: true });
    await testInfo.attach("dashboard-mobile-390x844", {
      path: testInfo.outputPath("dashboard-mobile-390x844.png"),
      contentType: "image/png",
    });
  });

  test("hamburger opens the drawer and navigates to Profile", async ({ page }, testInfo) => {
    await page.goto("/dashboard");
    await expect(page.getByTestId("applications-grid")).toBeVisible({ timeout: 10000 });

    // The drawer is not mounted/visible until opened.
    await expect(page.getByTestId("mobile-nav-drawer")).toBeHidden();

    await page.getByTestId("mobile-nav-hamburger").click();
    const drawer = page.getByTestId("mobile-nav-drawer");
    await expect(drawer).toBeVisible({ timeout: 5000 });

    await page.screenshot({ path: testInfo.outputPath("mobile-nav-drawer-open.png"), fullPage: true });
    await testInfo.attach("mobile-nav-drawer-open", {
      path: testInfo.outputPath("mobile-nav-drawer-open.png"),
      contentType: "image/png",
    });

    // Tap a nav item — the drawer both navigates AND closes itself.
    await drawer.getByTestId("nav-item-profile").click();
    await expect(page).toHaveURL(/\/profile$/, { timeout: 5000 });
    await expect(drawer).toBeHidden({ timeout: 5000 });
  });
});
