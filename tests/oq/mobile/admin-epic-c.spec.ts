// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Epic C admin sections at 390 px (mobile-chromium lane; c2 mocks frame 2 of
 * each page): the document never scrolls sideways — tables become cards, the
 * sub-nav scrolls inside itself — and the key element of each page is visible.
 */

import { test, expect } from "../../support/auth-fixture";
import type { Page } from "@playwright/test";
import { stubEpicC } from "../admin-epic-c-fixtures";

async function noSidewaysScroll(page: Page) {
  const device = page.viewportSize()!.width;
  const doc = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(doc).toBeLessThanOrEqual(device);
}

const PAGES: [string, string][] = [
  ["/admin/overview", "admin-health-strip"],
  ["/admin/settings", "admin-settings-provider-apply"],
  ["/admin/usage", "admin-usage-card"],
  ["/admin/audit", "admin-audit-card"],
];

test.describe("Epic C admin at 390 px", () => {
  for (const [path, testId] of PAGES) {
    test(`${path} fits the phone`, async ({ page }) => {
      await stubEpicC(page);
      await page.goto(path);
      await expect(page.getByTestId(testId).first()).toBeVisible();
      await noSidewaysScroll(page);
    });
  }

  test("the settings switch dialog fits the phone", async ({ page }) => {
    await stubEpicC(page);
    await page.goto("/admin/settings");
    await page.getByTestId("admin-settings-provider-select").selectOption("openrouter");
    await page.getByTestId("admin-settings-key-input").fill("sk-or-v1-SYNTHETIC");
    await page.getByTestId("admin-settings-provider-apply").click();
    const box = await page.getByTestId("admin-settings-switch-dialog").boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    await noSidewaysScroll(page);
  });
});
