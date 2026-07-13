// tests/e2e/oq/gaps-page.spec.ts
import { test, expect } from "@playwright/test";

/**
 * Gaps Page — OQ Tests
 *
 * Covers:
 *  - Gap categories render with correct severity dot colors
 *    (Cat B = yellow/warning, Cat C = red/critical)
 *  - "Generate CV Now" button calls advance API and navigates to CV page
 *  - "Quick Interview" button visible for new users with gaps, navigates to interview page
 *  - Error state renders when advance API fails
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const FLOW_ID = "flow-test-0000-0000-0000-000000000001";
const JOB_ID = "job-test-0000-0000-0000-000000000002";
const GAP_ID = "gap-test-0000-0000-0000-000000000003";

const MOCK_FLOW_STATE = {
  job_id: JOB_ID,
  user_type: "new",
  available_actions: {},
  gap_summary: { gap_analysis_id: GAP_ID },
  job_summary: { role_title: "Senior Software Engineer" },
};

const CLUSTER_ID_C = "cluster-test-c-0000-0000-0000-000000000005";
const CLUSTER_ID_B = "cluster-test-b-0000-0000-0000-000000000006";

const MOCK_GAP_ANALYSIS = {
  id: GAP_ID,
  match_score: 0.72,
  category_a: ["Python", "FastAPI"],
  category_b: ["Docker", "PostgreSQL"],
  category_c: ["Kubernetes", "Terraform"],
  strengths: ["Python", "FastAPI"],
  gap_clusters: [
    {
      id: CLUSTER_ID_C,
      label: "Container Orchestration",
      category: "C",
      gaps: ["Kubernetes", "Terraform"],
      jd_skills: ["Kubernetes"],
      jd_context: "Required for production deployments",
    },
    {
      id: CLUSTER_ID_B,
      label: "Database Operations",
      category: "B",
      gaps: ["Docker", "PostgreSQL"],
      jd_skills: ["Docker"],
      jd_context: "Nice to have for containerised deployments",
    },
  ],
};

const MOCK_PROFILE = {
  positions_count: 5,
  projects_count: 12,
  certifications_count: 3,
  data_points_count: 47,
};

const MOCK_ADVANCE_RESPONSE = { status: "ok" };

async function setupGapsPageMocks(page: import("@playwright/test").Page) {
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_STATE) })
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) })
  );
  await page.route(`**/api/profile`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
  );
}

test.describe("Gaps page", () => {
  test("renders gap categories with correct severity dot colors", async ({ page }) => {
    await setupGapsPageMocks(page);
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    // Cluster cards must be visible
    const cards = page.getByTestId("gap-cluster-card");
    await expect(cards.first()).toBeVisible();

    // Category C cluster card — dot must use bg-red-500 (not yellow)
    const catCCard = page.getByTestId("gap-cluster-card").filter({ hasText: "Container Orchestration" });
    await expect(catCCard).toBeVisible();
    const catCDot = catCCard.locator("span.rounded-full").first();
    const catCClass = await catCDot.getAttribute("class");
    expect(catCClass).toContain("bg-red-500");
    expect(catCClass).not.toContain("bg-yellow");

    // Category B cluster card — dot must use bg-yellow-400 (not red)
    const catBCard = page.getByTestId("gap-cluster-card").filter({ hasText: "Database Operations" });
    await expect(catBCard).toBeVisible();
    const catBDot = catBCard.locator("span.rounded-full").first();
    const catBClass = await catBDot.getAttribute("class");
    expect(catBClass).toContain("bg-yellow-400");
    expect(catBClass).not.toContain("bg-red");
  });

  test("shows correct gap counts in badges", async ({ page }) => {
    await setupGapsPageMocks(page);
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    // Match score display
    await expect(page.getByTestId("match-score-display")).toContainText("72%");

    // Gaps section shows the canonical gap count = number of gap clusters (E037 F1:
    // the heading matches the badge). The 4 Cat B+C skills collapse into 2 clusters.
    await expect(page.getByTestId("gaps-section")).toBeVisible();
    await expect(page.getByTestId("gaps-section")).toContainText("2 gaps identified");
  });

  test("Generate CV Now button advances flow and navigates to CV page", async ({ page }) => {
    await setupGapsPageMocks(page);

    let advanceCalled = false;
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) => {
      advanceCalled = true;
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADVANCE_RESPONSE) });
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("generate-cv-button").click();

    await expect(page).toHaveURL(`/flow/${FLOW_ID}/cv`, { timeout: 10000 });
    expect(advanceCalled).toBe(true);
  });

  test("Quick Interview button visible for new user with gaps and navigates to interview", async ({ page }) => {
    await setupGapsPageMocks(page);

    const SESSION_ID = "session-test-0000-0000-0000-000000000004";
    await page.route(`**/api/session`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION_ID, id: SESSION_ID }),
      })
    );
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADVANCE_RESPONSE) })
    );

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    const interviewButton = page.getByTestId("interview-button");
    await expect(interviewButton).toBeVisible();
    await interviewButton.click();

    await expect(page).toHaveURL(`/flow/${FLOW_ID}/interview`, { timeout: 10000 });
  });

  test("Quick Interview offered to RETURNING user with gaps; onboarding hero hidden (ADR-016 amended)", async ({ page }) => {
    // Spaghettieis UAT: the follow-up flow (returning + job) hid both the gap
    // mitigation path and must not open with the onboarding "Master Profile
    // Created" summary.
    await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...MOCK_FLOW_STATE, user_type: "returning", created_at: "2026-07-13T10:00:00Z" }),
      })
    );
    await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) })
    );
    await page.route(`**/api/profile`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
    );

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await expect(page.getByTestId("interview-button")).toBeVisible();
    await expect(page.getByTestId("generate-cv-button")).toBeVisible();
    // No onboarding chrome on a JD-only follow-up.
    await expect(page.getByText(/Master Profile Created|Master-Profil erstellt/)).toHaveCount(0);
  });

  test("clicking a gap cluster card starts the micro-session inline", async ({ page }) => {
    await setupGapsPageMocks(page);

    const QUESTION = "Beschreibe deine Erfahrung mit Kubernetes.";
    await page.route(`**/api/session`, (route) => {
      const body = route.request().postDataJSON();
      if (body?.mode === "targeted") {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session_id: "micro-session-001",
            first_question: QUESTION,
            choices: null,
          }),
        });
      } else {
        route.continue();
      }
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    // Card should be clickable (cursor-pointer) in idle state
    const card = page.getByTestId("gap-cluster-card").first();
    await expect(card).toBeVisible();
    await card.click();

    // Question panel should appear inline
    await expect(page.getByTestId("gap-question")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("gap-question")).toContainText(QUESTION);

    // Card is no longer clickable once session is open
    const classAttr = await card.getAttribute("class");
    expect(classAttr).not.toContain("cursor-pointer");
  });

  test("shows error message when advance API fails", async ({ page }) => {
    await setupGapsPageMocks(page);
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid step transition" }),
      })
    );

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("generate-cv-button").click();

    await expect(page.getByTestId("error-message")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("error-message")).toContainText("Invalid step transition");
  });
});
