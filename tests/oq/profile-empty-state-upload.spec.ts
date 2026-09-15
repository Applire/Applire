import { test, expect } from "@playwright/test";

/**
 * #704 (founder UAT on the Nougat RC, 2026-09-15) — a first-time user with no
 * Master Profile saw a dead end ("No profile found. Please import a CV
 * first." + "Back to Home") with no way to actually import a CV from that
 * page. This drives the real profile page + the real ProfileImportView
 * (the same upload dropzone the welcome screen offers) through a mocked
 * 404 on /api/profile — the same `page.route` pattern #604's conflict spec
 * established.
 */

async function openEmptyProfile(page: import("@playwright/test").Page) {
  await page.route("**/api/profile", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({}) }),
  );
  await page.route("**/api/profile/enrichment-history", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/profile/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/profile/uploads", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.goto("/profile");
}

test.describe("#704 — the empty profile page offers the CV upload", () => {
  test("shows the upload dropzone and a dashboard link instead of a dead end", async ({ page }) => {
    await openEmptyProfile(page);

    // The dropzone itself is the load signal (avoids networkidle, which
    // never settles — Google Fonts in the root layout).
    await expect(page.getByTestId("main-file-input")).toHaveCount(1, { timeout: 30000 });
    await expect(page.getByTestId("main-upload-button")).toBeVisible();
    await expect(page.getByRole("button", { name: /back to home/i })).toBeVisible();

    // The dropzone's OWN AppTopbar must not stack on top of the profile
    // page's own chrome — hideTopbar suppresses it (#704).
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  });

  test("renders the German heading and upload copy under the de locale", async ({ page }) => {
    await page.route("**/api/settings", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ui_language: "de", ui_language_explicit: true, dismissed_explainers: [] }),
      }),
    );
    await openEmptyProfile(page);

    await expect(page.getByTestId("main-file-input")).toHaveCount(1, { timeout: 30000 });
    const text = (await page.locator("main").first().innerText()).replace(/\s+/g, " ");
    expect(text).toContain("Noch kein Profil");
    expect(text).toContain("Zurück zur Startseite");
  });
});
