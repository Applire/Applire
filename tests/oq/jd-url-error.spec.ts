// tests/oq/jd-url-error.spec.ts
/**
 * Branch F — JD URL Fetch Failure (OQ tier)
 *
 * Verifies the full error recovery flow when a job description URL fails.
 * Since #151 a blocked/invalid JD URL PAUSES the overlay with an inline
 * paste affordance — continuing without a job is an explicit user choice,
 * never the default. All backend calls are mocked — no real LLM or scraper.
 *
 * Run: npx playwright test tests/oq/jd-url-error.spec.ts
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

  // Shared onboarding steps: fill the blocked URL, attach a CV, submit.
  async function startBlockedOnboarding(page: import("@playwright/test").Page) {
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
  }

  test("blocked scrape PAUSES the overlay with an inline paste affordance (#151 — no silent continue)", async ({
    page,
  }) => {
    await startBlockedOnboarding(page);

    // The pause-and-paste recovery block appears with the honest message
    await expect(page.getByTestId("jd-paste-recovery")).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText(
        "The site blocked us from reading that job posting. Paste the job description below to continue."
      )
    ).toBeVisible();
    await expect(page.getByTestId("jd-paste-textarea")).toBeVisible();
    await expect(page.getByTestId("jd-paste-submit")).toBeVisible();
    await expect(page.getByTestId("jd-skip-button")).toBeVisible();

    // Hard error block must NOT appear
    await expect(page.getByTestId("processing-error")).not.toBeVisible();

    // And the pipeline did NOT auto-continue: still on the landing page, no redirect.
    await page.waitForTimeout(1500);
    await expect(page).not.toHaveURL(new RegExp(`/flow/`));
  });

  test("pasting the JD inline continues the pipeline with the analyzed job (#151)", async ({
    page,
  }) => {
    const PASTED_FLOW_ID = "mock-flow-pasted";
    const JOB_ID = "job-oq-pasted";

    // Later routes win in Playwright: re-route JD analyze so the URL attempt
    // stays blocked (422) but the pasted TEXT succeeds.
    await page.route("**/api/job/analyze", async (route) => {
      const body = route.request().postData() ?? "";
      if (body.includes('"text"')) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ id: JOB_ID, role_title: "QA Engineer" }),
        });
      } else {
        await route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            detail: { error_code: "jd_fetch_failed", message: "blocked" },
          }),
        });
      }
    });
    // Application creation from the analyzed job → flow with the job linked
    await page.route("**/api/applications", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ flow_session_id: PASTED_FLOW_ID }),
      });
    });
    await page.route(`**/api/flow/${PASTED_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: JOB_ID,
          user_type: "new",
          available_actions: {},
          gap_summary: null,
          job_summary: { role_title: "QA Engineer" },
        }),
      });
    });
    // Async gap analysis: 202 handle then ready poll
    await page.route(`**/api/job/${JOB_ID}/gap-jobs`, async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ gap_job_id: "gj-1", status: "pending" }),
      });
    });
    await page.route(`**/api/job/${JOB_ID}/gap-jobs/*`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ready",
          error_code: null,
          result: { id: "gap-1", match_score: 0.8, category_a: [], category_b: [], category_c: [], strengths: [], gap_clusters: [] },
        }),
      });
    });
    await page.route(`**/api/flow/${PASTED_FLOW_ID}/advance`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await startBlockedOnboarding(page);

    await expect(page.getByTestId("jd-paste-textarea")).toBeVisible({ timeout: 10000 });
    await page
      .getByTestId("jd-paste-textarea")
      .fill("QA Engineer at Acme GmbH. 5 years of testing experience required.");
    await page.getByTestId("jd-paste-submit").click();

    // The pipeline resumes with the job — gaps redirect WITHOUT jd_status.
    await expect(page).toHaveURL(new RegExp(`/flow/${PASTED_FLOW_ID}/gaps(?!\\?jd_status)`), {
      timeout: 15000,
    });
  });

  test("explicit 'continue without job description' preserves the JD-less flow and redirects with ?jd_status=fetch_failed", async ({
    page,
  }) => {
    await startBlockedOnboarding(page);

    await expect(page.getByTestId("jd-skip-button")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("jd-skip-button").click();

    // The amber skipped note appears (old behaviour, now explicit)
    await expect(
      page.getByText("The site blocked us — you can paste the text later")
    ).toBeVisible({ timeout: 5000 });

    // Pipeline completes and redirects to gaps page with the query param
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
