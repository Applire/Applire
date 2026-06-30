// tests/e2e/oq/jd-url-error.spec.ts
/**
 * Branch F — JD URL Fetch Failure (Sprint 26, OQ tier)
 *
 * Verifies the full error recovery flow when a job description URL fails.
 * All backend calls are mocked — no real LLM or scraper required.
 *
 * Run: npx playwright test tests/e2e/oq/jd-url-error.spec.ts
 */

import { test, expect } from "@playwright/test";

const FLOW_ID = "mock-flow-sprint26";

test.describe("Branch F — JD URL fetch failure", () => {
  test.beforeEach(async ({ page }) => {
    // Check user state → new user (show onboarding form)
    await page.route("**/api/profile/exists", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ exists: false }),
      });
    });

    // JD analyze → 422 jd_fetch_failed
    await page.route("**/api/job/analyze", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              error_code: "jd_fetch_failed",
              message: "Could not extract job text from this page. Please paste the job description manually.",
            },
          }),
        });
      } else {
        await route.continue();
      }
    });

    // Flow creation (bare, no job)
    await page.route("**/api/flow", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({ flow_id: FLOW_ID }),
        });
      } else {
        await route.continue();
      }
    });

    // CV upload — async import job (E036): POST returns a handle (202), then the
    // overlay polls GET /import-jobs/{id} until ready. Mock both ends.
    await page.route("**/api/profile/import-jobs", async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ import_id: "imp-1", status: "pending" }),
      });
    });
    await page.route("**/api/profile/import-jobs/*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ready", error_code: null, result: { ok: true } }),
      });
    });

    // Flow state (needed when gaps page loads)
    await page.route(`**/api/flow/${FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: null,
          user_type: "new",
          available_actions: {},
          gap_summary: null,
          job_summary: null,
        }),
      });
    });
  });

  test("overlay shows JD step as skipped and redirects with ?jd_status=fetch_failed", async ({
    page,
  }) => {
    await page.goto("/");

    // The URL input is in "URL" mode by default
    const urlInput = page.locator('input[type="url"]');
    await expect(urlInput).toBeVisible();
    await urlInput.fill("https://blocked-job-site.example.com/posting/123");

    // Upload a fake CV file
    await page.getByTestId("file-input").setInputFiles({
      name: "test-cv.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("fake pdf content"),
    });

    // Submit
    await page.getByTestId("submit-button").click();

    // Processing overlay should appear
    await expect(page.getByTestId("processing-indicator")).toBeVisible({ timeout: 5000 });

    // JD step should show the amber skipped message (not hard error)
    await expect(
      page.getByText("The site blocked us — you can paste the text later")
    ).toBeVisible({ timeout: 10000 });

    // Hard error block must NOT appear
    await expect(page.getByTestId("processing-error")).not.toBeVisible();

    // Pipeline should complete and redirect to gaps page with query param
    await expect(page).toHaveURL(
      new RegExp(`/flow/${FLOW_ID}/gaps\\?jd_status=fetch_failed`),
      { timeout: 15000 }
    );
  });

  test("amber recovery banner is visible on gaps page with correct copy", async ({
    page,
  }) => {
    // Navigate directly to the gaps page with the query param (simulates the redirect)
    await page.goto(`/flow/${FLOW_ID}/gaps?jd_status=fetch_failed`);

    // Banner must appear
    const banner = page.getByTestId("jd-recovery-banner");
    await expect(banner).toBeVisible({ timeout: 5000 });
    await expect(banner).toContainText(
      "We couldn't load that job posting — it may be blocked or taken down."
    );
  });

  test("amber recovery banner shows url_invalid copy for jd_status=url_invalid", async ({
    page,
  }) => {
    await page.goto(`/flow/${FLOW_ID}/gaps?jd_status=url_invalid`);

    const banner = page.getByTestId("jd-recovery-banner");
    await expect(banner).toBeVisible({ timeout: 5000 });
    await expect(banner).toContainText("That URL didn't look valid.");
  });

  test("CTA navigates to dashboard", async ({ page }) => {
    await page.goto(`/flow/${FLOW_ID}/gaps?jd_status=fetch_failed`);

    await expect(page.getByTestId("jd-recovery-cta")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("jd-recovery-cta").click();

    // In-app home actions now route to /dashboard (the logged-in home), not onboarding.
    await expect(page).toHaveURL("/dashboard", { timeout: 5000 });
  });

  test("dismiss button hides the banner", async ({ page }) => {
    await page.goto(`/flow/${FLOW_ID}/gaps?jd_status=fetch_failed`);

    const banner = page.getByTestId("jd-recovery-banner");
    await expect(banner).toBeVisible({ timeout: 5000 });

    await page.getByTestId("jd-recovery-dismiss").click();

    await expect(banner).not.toBeVisible({ timeout: 3000 });
  });
});
