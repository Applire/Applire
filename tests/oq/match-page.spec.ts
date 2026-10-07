// tests/e2e/oq/match-page.spec.ts
import { test, expect } from "../support/auth-fixture";

/**
 * Match Page E2E Smoke Test (APP-16)
 *
 * Covers:
 *  - /match loads and shows at least one job card when the API returns results
 *  - Score bar and badges render correctly
 *  - "Run gap analysis" CTA navigates to the main flow page with ?job_id=…
 *  - Empty state renders when API returns an empty list
 *  - Redirects to "/" when API returns 404 (no profile yet)
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const TEST_JOB_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";

const MOCK_JOBS = [
  {
    job_id: TEST_JOB_ID,
    role_title: "Senior Software Engineer",
    company_name: "Acme GmbH",
    berufsbild_code: "43104",
    berufsbild_label: "Softwareentwicklung",
    llm_match_score: 0.85,
    embedding_similarity: 0.78,
    combined_score: 0.82,
    gap_analysis_id: "gap-001",
  },
  {
    job_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    role_title: "Frontend Developer",
    company_name: "Beta AG",
    berufsbild_code: null,
    berufsbild_label: null,
    llm_match_score: 0.5,
    embedding_similarity: null,
    combined_score: 0.45,
    gap_analysis_id: null,
  },
];

test.describe("/match page", () => {
  test("shows job cards when API returns results", async ({ page }) => {
    await page.route("**/api/jobs/match**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_JOBS),
      })
    );

    await page.goto("/match");

    // Job card list should be visible
    const cardList = page.getByTestId("job-card-list");
    await expect(cardList).toBeVisible({ timeout: 10000 });

    // Both cards rendered
    const cards = page.getByTestId("job-card");
    await expect(cards).toHaveCount(2);

    // First card: title and company
    const firstCard = cards.first();
    await expect(firstCard.getByTestId("job-card-title")).toHaveText("Senior Software Engineer");
    await expect(firstCard.getByTestId("job-card-company")).toHaveText("Acme GmbH");

    // Score displayed as percentage
    await expect(firstCard.getByTestId("job-card-score")).toHaveText("82%");

    // Score bar fill should have a width style
    const fill = firstCard.getByTestId("score-bar-fill");
    await expect(fill).toBeVisible();
    const width = await fill.evaluate((el) => (el as HTMLElement).style.width);
    expect(width).toBe("82%");

    // Berufsbild badge
    await expect(firstCard.getByTestId("berufsbild-badge")).toHaveText("Softwareentwicklung");

    // Seniority badge
    await expect(firstCard.getByTestId("seniority-badge")).toHaveText("Senior");
  });

  test("'Run gap analysis' CTA navigates to /?job_id=…", async ({ page }) => {
    await page.route("**/api/jobs/match**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_JOBS),
      })
    );

    await page.goto("/match");

    // Wait for cards
    await expect(page.getByTestId("job-card-list")).toBeVisible({ timeout: 10000 });

    // Click CTA on first card
    const firstCard = page.getByTestId("job-card").first();
    await firstCard.getByTestId("run-gap-analysis-btn").click();

    // Should navigate to home page with job_id query param
    await expect(page).toHaveURL(new RegExp(`\\?job_id=${TEST_JOB_ID}`), { timeout: 5000 });
  });

  test("shows empty state when API returns no jobs", async ({ page }) => {
    await page.route("**/api/jobs/match**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      })
    );

    await page.goto("/match");

    await expect(page.getByTestId("match-empty-state")).toBeVisible({ timeout: 10000 });
    // No job cards
    await expect(page.getByTestId("job-card")).toHaveCount(0);
  });

  test("redirects away from /match when API returns 404 (no profile)", async ({ page }) => {
    await page.route("**/api/jobs/match**", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "No master profile found." }),
      })
    );

    await page.goto("/match");

    // Match page redirects to '/'. For returning users the root page then
    // onwards to /dashboard — accept any non-/match destination as correct.
    await page.waitForURL((url) => !url.pathname.startsWith("/match"), { timeout: 10000 });
    expect(page.url()).not.toContain("/match");
  });
});
