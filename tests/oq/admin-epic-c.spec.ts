// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration, Epic C (#694 #710 #726 #738, S-11, B2-2; c2 mocks) against a
 * mocked API (docs/dev/api-contract-admin.md). Pins: the overview is the
 * landing; the settings form sends ONE atomic PUT with provider + key and the
 * key is not in the page afterwards; switching deletion off asks first; usage
 * and audit filters reach the request; a non-admin gets the "for admins" card
 * and no admin request leaves the browser.
 */

import { test, expect, REGULAR_USER } from "../support/auth-fixture";
import { stubEpicC } from "./admin-epic-c-fixtures";

test.describe("Administration — Epic C", () => {
  test("/admin lands on the overview; health line, tiles, failed jobs, config and activity render", async ({ page }) => {
    await stubEpicC(page);
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/admin\/overview$/);
    await expect(page.getByTestId("admin-nav-overview")).toHaveAttribute("aria-current", "page");
    for (const k of ["overview", "users", "usage", "settings", "audit", "appearance", "monitoring"]) {
      await expect(page.getByTestId(`admin-nav-${k}`)).toBeVisible();
    }
    await expect(page.getByTestId("admin-health-strip")).toHaveAttribute("data-status", "degraded");
    await expect(page.getByTestId("admin-health-chip-backup")).toBeVisible();
    await expect(page.getByTestId("admin-overview-tile-people")).toContainText("4");
    await expect(page.getByTestId("admin-overview-failed-row")).toHaveCount(3);
    await expect(page.getByTestId("admin-overview-config-provider")).toContainText("requesty");
    await expect(page.getByTestId("admin-audit-card")).toHaveCount(3);
    await expect(page.getByTestId("admin-overview-top-user")).toHaveCount(3);
  });

  test("the sidebar's Administration entry opens the overview", async ({ page }) => {
    await stubEpicC(page);
    await page.goto("/settings");
    await page.getByTestId("sidebar-nav-admin").click();
    await expect(page).toHaveURL(/\/admin\/overview$/);
  });

  test("switching provider with a key is one PUT after confirmation, and the key does not stay in the page", async ({ page }) => {
    const log = await stubEpicC(page);
    await page.goto("/admin/settings");
    await expect(page.getByTestId("admin-settings-provider-source")).toHaveAttribute("data-source", "env");
    await expect(page.getByTestId("admin-settings-qualification")).toHaveAttribute("data-qualification", "qualified");
    await expect(page.getByTestId("admin-settings-dep-ocr")).toHaveAttribute("data-satisfied", "false");
    await page.getByTestId("admin-settings-provider-select").selectOption("openrouter");
    await page.getByTestId("admin-settings-key-input").fill("sk-or-v1-SYNTHETIC");
    await page.getByTestId("admin-settings-provider-apply").click();
    await expect(page.getByTestId("admin-settings-switch-dialog")).toBeVisible();
    expect(log.filter((r) => r.method === "PUT")).toHaveLength(0);
    await page.getByTestId("admin-settings-switch-confirm").click();
    await expect(page.getByTestId("admin-settings-saved")).toBeVisible();
    const puts = log.filter((r) => r.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(JSON.parse(puts[0].body!)).toEqual({ changes: { LLM_PROVIDER: "openrouter", OPENROUTER_API_KEY: "sk-or-v1-SYNTHETIC" } });
    await expect(page.getByTestId("admin-settings-provider-source")).toHaveAttribute("data-source", "panel");
    expect(await page.content()).not.toContain("sk-or-v1-SYNTHETIC");
    await page.getByTestId("admin-settings-provider-reset").click();
    await expect.poll(() => log.some((r) => r.method === "DELETE" && r.url === "/api/admin/settings/LLM_PROVIDER")).toBe(true);
  });

  test("LinkedIn switches at once; deletion OFF asks first and then shows what keeps working", async ({ page }) => {
    const log = await stubEpicC(page);
    await page.goto("/admin/settings");
    await page.getByTestId("admin-settings-linkedin-toggle").click();
    await expect(page.getByTestId("admin-settings-linkedin-toggle")).toHaveAttribute("aria-checked", "false");
    await page.getByTestId("admin-settings-retention-toggle").click();
    await expect(page.getByTestId("admin-settings-retention-dialog")).toBeVisible();
    await page.getByTestId("admin-settings-retention-confirm").click();
    await expect(page.getByTestId("admin-settings-retention-off")).toBeVisible();
    await expect(page.getByTestId("admin-settings-retention-others")).toContainText("3");
    const bodies = log.filter((r) => r.method === "PUT").map((r) => JSON.parse(r.body!));
    expect(bodies).toEqual([{ changes: { SCRAPER_FETCH_LINKEDIN_GUEST_PAGES: false } }, { changes: { RETENTION_ENABLED: false } }]);
  });

  test("usage lists every account and asks for the chosen window", async ({ page }) => {
    const log = await stubEpicC(page);
    await page.goto("/admin/usage");
    await expect(page.getByTestId("admin-usage-row")).toHaveCount(4);
    await expect(page.getByTestId("admin-usage-unattributed")).toBeVisible();
    await expect(page.getByTestId("admin-usage-by-kind")).toBeVisible();
    await page.getByTestId("admin-usage-period-7").click();
    await expect.poll(() => log.some((r) => r.url === "/api/admin/usage?days=7")).toBe(true);
  });

  test("audit: rows, a category filter with the server's action names, and load older", async ({ page }) => {
    const log = await stubEpicC(page);
    await page.goto("/admin/audit");
    await expect(page.getByTestId("admin-audit-row")).toHaveCount(7);
    await expect(page.getByTestId("admin-audit-row").nth(2)).not.toContainText("sk-");
    await page.getByTestId("admin-audit-load-more").click();
    await expect(page.getByTestId("admin-audit-row")).toHaveCount(8);
    await page.getByTestId("admin-audit-filter-settings").click();
    await expect.poll(() => log.some((r) => r.url.includes("action=settings.changed&action=settings.reset&action=settings.env_observed"))).toBe(true);
    await expect(page.getByTestId("admin-audit-row")).toHaveCount(2);
  });
});

test.describe("Administration — not an admin", () => {
  test.use({ authUser: REGULAR_USER });

  test("a signed-in non-admin gets the 'for admins' card, no sub-nav, no admin request", async ({ page }) => {
    const log = await stubEpicC(page);
    await page.goto("/admin/settings");
    await expect(page.getByTestId("admin-forbidden")).toBeVisible();
    await expect(page.getByTestId("sidebar-nav-admin")).toHaveCount(0);
    await expect(page.getByTestId("admin-nav-settings")).toHaveCount(0);
    // `/api/admin/color-schemes/active` is the public theme read every page makes (MD-6).
    expect(log.filter((r) => r.url.startsWith("/api/admin/") && r.url !== "/api/admin/color-schemes/active")).toEqual([]);
    await page.getByTestId("admin-forbidden-back").click();
    await expect(page).toHaveURL(/\/dashboard/);
  });
});

test.describe("Dashboard — instance signal (#694)", () => {
  test("an admin sees one line with the notice count that opens the overview", async ({ page }) => {
    await stubEpicC(page, { notices: [{ code: "health_degraded", severity: "warning" }, { code: "failed_jobs", severity: "warning" }] });
    await page.goto("/dashboard");
    await expect(page.getByTestId("instance-signal")).toHaveAttribute("data-critical", "false");
    await expect(page.getByTestId("instance-signal")).toContainText("2");
    await page.getByTestId("instance-signal-link").click();
    await expect(page).toHaveURL(/\/admin\/overview$/);
  });

  test("nothing to say → no line", async ({ page }) => {
    await stubEpicC(page, { notices: [] });
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await expect(page.getByTestId("instance-signal")).toHaveCount(0);
  });
});

test.describe("Dashboard — instance signal, not an admin", () => {
  test.use({ authUser: REGULAR_USER });

  test("a non-admin never asks for notices", async ({ page }) => {
    const log = await stubEpicC(page, { notices: [{ code: "health_down", severity: "critical" }] });
    await page.goto("/dashboard");
    await page.waitForTimeout(1500);
    await expect(page.getByTestId("instance-signal")).toHaveCount(0);
    expect(log.some((r) => r.url === "/api/admin/notices")).toBe(false);
  });
});
