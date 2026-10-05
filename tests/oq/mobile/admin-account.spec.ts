// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Strawberry admin + account screens at 390 px (mobile-chromium lane).
 *
 * Found on the 2b real-path screenshot (2026-10-05): the people table's `sr-only`
 * actions header was absolutely positioned against the PAGE (its scroll wrapper
 * was not `relative`), so the 390 px layout grew to 841 px and the add-person
 * dialog opened half off-screen. Pinned here as "the document never scrolls
 * sideways"; the wide tables scroll inside their own card.
 */

import { test, expect } from "../../support/auth-fixture";
import type { Page, Route } from "@playwright/test";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const NOW = new Date().toISOString();

const USERS = ["anna.bauer@example.org", "jonas.keller@example.org"].map((email, i) => ({
  id: `00000000-0000-0000-0000-00000000000${i + 1}`,
  email,
  role: i === 0 ? "admin" : "user",
  status: "active",
  created_at: NOW,
  last_login_at: NOW,
  last_active_at: NOW,
  invite_expires_at: null,
  metadata: { application_count: 3, document_count: 2, storage_bytes: 12_000_000, ai_tokens_30d: 42_000 },
}));

async function stubAll(page: Page) {
  await page.route("**/api/admin/users", (route) => json(route, { users: USERS }));
  await page.route("**/api/admin/probe-tokens", (route) =>
    json(route, {
      tokens: [{ id: "t1", name: "Uptime Kuma", scope: "probe", prefix: "m4r8s1qa", created_at: NOW, last_used_at: null }],
    }),
  );
  await page.route("**/api/me/tokens", (route) =>
    json(route, {
      tokens: [{ id: "t2", name: "Claude Desktop (Laptop)", scope: "agent", prefix: "3kq8x2mz", created_at: NOW, last_used_at: NOW }],
    }),
  );
  await page.route("**/api/ops/health", (route) =>
    json(route, { status: "ok", edition: "community", version: "0.43.0", llm_provider: "mock", checked_at: NOW, components: {}, usage: {} }),
  );
  await page.route("**/api/settings", (route) => json(route, { ui_language: "en", target_cv_pages: null }));
}

async function noSidewaysScroll(page: Page) {
  // Against the DEVICE width: under mobile emulation an overflowing page widens the
  // layout viewport itself, so `window.innerWidth` grows with the defect (841 = 841).
  const device = page.viewportSize()!.width;
  const doc = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(doc).toBeLessThanOrEqual(device);
}

test.describe("Admin + account at 390 px", () => {
  for (const [path, ready] of [
    ["/admin/users", "admin-users-table"],
    ["/admin/monitoring", "monitoring-table"],
    ["/settings", "tokens-agent-table"],
  ] as const) {
    test(`${path} never scrolls sideways`, async ({ page }) => {
      await stubAll(page);
      await page.goto(path);
      await expect(page.getByTestId(ready)).toBeVisible();
      await noSidewaysScroll(page);
    });
  }

  test("the add-person dialog fits the screen and the row menu stays open", async ({ page }) => {
    await stubAll(page);
    await page.goto("/admin/users");
    await page.getByTestId("admin-add-open").click();
    const dialog = page.getByTestId("admin-add-dialog");
    await expect(dialog).toBeVisible();
    const box = await dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    await page.keyboard.press("Escape");

    await page.locator('[data-email="jonas.keller@example.org"]').getByTestId("admin-user-menu").click();
    await expect(page.getByTestId("admin-user-menu-list")).toBeVisible();
    await expect(page.getByTestId("admin-user-action-delete")).toBeInViewport();
  });
});
