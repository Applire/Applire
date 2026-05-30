import { test, expect } from "@playwright/test";

test.describe("Admin appearance page", () => {
  test("loads the appearance page with scheme editor and preview", async ({ page }) => {
    await page.route("**/api/admin/color-schemes", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "scheme-eu-blue-0000-0000-0000-000000000001",
            name: "EU Blue",
            is_active: true,
            is_builtin: true,
            seed_primary: "#003399",
            seed_accent: "#FFCC00",
            seed_secondary: "#003399",
            surface_lightness: 0.97,
            derived: {},
          },
        ]),
      })
    );
    await page.goto("/admin/appearance");
    // Header
    await expect(page.getByRole("heading", { name: "Appearance" })).toBeVisible();
    // Left panel sections
    await expect(page.getByText("Saved Schemes")).toBeVisible();
    await expect(page.getByText("Seed Colors")).toBeVisible();
    await expect(page.getByText("Surface Tint")).toBeVisible();
    await expect(page.getByText("Save Scheme")).toBeVisible();
    // Right panel
    await expect(page.getByText("Live Preview")).toBeVisible();
    // EU Blue scheme should be loaded in the preset picker
    await expect(page.getByText("EU Blue")).toBeVisible();
  });

  test("sidebar Admin entry navigates to appearance", async ({ page }) => {
    await page.goto("/settings");
    // Admin moved from a footer link to a persistent sidebar nav button.
    const adminNav = page.getByRole("button", { name: /admin/i });
    await expect(adminNav).toBeVisible();
    await adminNav.click();
    await expect(page).toHaveURL(/\/admin\/appearance/);
  });

  test("opening the editor keeps the active palette (no neutral-placeholder flash)", async ({ page }) => {
    // EU Blue active. Opening the editor must NOT stamp the neutral #4a4a4a
    // placeholder onto the global document — it should seed from the active scheme.
    await page.route("**/api/admin/color-schemes", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "scheme-eu-blue-0000-0000-0000-000000000001",
            name: "EU Blue",
            is_active: true,
            is_builtin: true,
            seed_primary: "#003399",
            seed_accent: "#ffcc00",
            seed_secondary: "#c9a84c",
            surface_lightness: 0.8,
            derived: {},
          },
        ]),
      })
    );
    await page.goto("/admin/appearance");
    await expect(page.getByText("Saved Schemes")).toBeVisible();
    // --color-primary must reflect EU Blue's seed, never the neutral placeholder (#4a4a4a).
    const primary = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--color-primary").trim().toLowerCase()
    );
    expect(primary).toBe("#003399");
    expect(primary).not.toBe("#4a4a4a");
  });

  test("ThemeProvider injects CSS custom properties on app load", async ({ page }) => {
    await page.goto("/");
    const primaryColor = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--color-primary").trim()
    );
    // Should be set (non-empty) — exact value depends on active scheme
    expect(primaryColor).toBeTruthy();
    expect(primaryColor).toMatch(/^#[0-9a-f]{6}$/i);
  });
});
