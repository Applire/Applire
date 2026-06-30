// tests/e2e/oq/upload-flow.spec.ts
import { test, expect, Page } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";

/**
 * Upload Flow — OQ Tests
 *
 * Covers:
 *  - Home page renders upload form for new users
 *  - Submit button is disabled until a CV file is selected
 *  - Submitting triggers the processing overlay
 *  - After all pipeline steps complete, navigates to /flow/:id/gaps
 *  - Error state renders when the JD analysis API fails
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_CV = path.join(__dirname, "../fixtures/profiles/sample_cv.pdf");

const FLOW_ID = "flow-upload-0000-0000-0000-000000000001";
const JOB_ID = "job-upload-0000-0000-0000-000000000002";
const GAP_ID = "gap-upload-0000-0000-0000-000000000003";

const MOCK_JD_ANALYZE = { id: JOB_ID, role_title: "Senior Software Engineer" };
const MOCK_FLOW_CREATE = { flow_id: FLOW_ID };
const MOCK_UPLOAD = { success: true };
const MOCK_FLOW_STATE = {
  job_id: JOB_ID,
  user_type: "new",
  available_actions: {},
  gap_summary: null,
  job_summary: { role_title: "Senior Software Engineer" },
};
const MOCK_GAP_ANALYSIS = {
  id: GAP_ID,
  match_score: 0.78,
  category_a: ["Python"],
  category_b: ["Docker"],
  category_c: ["Kubernetes"],
  strengths: ["Python"],
};
const MOCK_ADVANCE = { status: "ok" };
const MOCK_APP_CREATE = { flow_session_id: FLOW_ID };

async function setupUploadMocks(page: Page) {
  await page.route("**/api/profile/exists", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ exists: false }) })
  );
  await page.route("**/api/job/analyze", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_JD_ANALYZE) })
  );
  // When jobId is non-null the overlay calls POST /api/applications (not /api/flow)
  await page.route("**/api/applications", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_APP_CREATE) })
  );
  await page.route("**/api/flow", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_CREATE) })
  );
  // Async import job (E036): POST returns a handle (202), then poll until ready.
  await page.route("**/api/profile/import-jobs", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ import_id: "imp-1", status: "pending" }) })
  );
  await page.route("**/api/profile/import-jobs/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ready", error_code: null, result: MOCK_UPLOAD }) })
  );
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_STATE) })
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) })
  );
  await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADVANCE) })
  );
}

test.describe("Upload flow", () => {
  test("submit button is disabled without a CV file", async ({ page }) => {
    await page.route("**/api/profile/exists", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ exists: false }) })
    );
    await page.goto("/");
    await expect(page.getByTestId("submit-button")).toBeDisabled({ timeout: 10000 });
  });

  test("submit button enables after uploading a CV file", async ({ page }) => {
    await page.route("**/api/profile/exists", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ exists: false }) })
    );
    await page.goto("/");
    await expect(page.getByTestId("submit-button")).toBeDisabled({ timeout: 10000 });

    await page.getByTestId("file-input").setInputFiles(SAMPLE_CV);

    await expect(page.getByTestId("submit-button")).toBeEnabled({ timeout: 5000 });
  });

  test("submitting shows processing overlay and navigates to gaps page", async ({ page }) => {
    await setupUploadMocks(page);
    await page.goto("/");

    // Switch to paste-text JD mode and fill in text
    await page.getByRole("button", { name: "Paste Text" }).click();
    await page.locator("textarea").fill("We are looking for a Senior Software Engineer with Python and Docker experience.");

    // Upload CV
    await page.getByTestId("file-input").setInputFiles(SAMPLE_CV);
    await expect(page.getByTestId("submit-button")).toBeEnabled({ timeout: 5000 });

    // Submit
    await page.getByTestId("submit-button").click();

    // Processing overlay should appear
    await expect(page.getByTestId("processing-indicator")).toBeVisible({ timeout: 5000 });

    // Eventually navigates to gaps page
    await expect(page).toHaveURL(new RegExp(`/flow/${FLOW_ID}/gaps`), { timeout: 30000 });
  });

  test("shows error when JD analysis API fails", async ({ page }) => {
    await page.route("**/api/profile/exists", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ exists: false }) })
    );
    await page.route("**/api/job/analyze", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "LLM service unavailable" }),
      })
    );
    await page.route("**/api/flow", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_CREATE) })
    );
    await page.route("**/api/profile/import-jobs", (route) =>
      route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ import_id: "imp-1", status: "pending" }) })
    );
    await page.route("**/api/profile/import-jobs/*", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ready", error_code: null, result: MOCK_UPLOAD }) })
    );

    await page.goto("/");
    await page.getByRole("button", { name: "Paste Text" }).click();
    await page.locator("textarea").fill("Some job description text.");
    await page.getByTestId("file-input").setInputFiles(SAMPLE_CV);
    await page.getByTestId("submit-button").click();

    // Processing overlay appears then shows error
    await expect(page.getByTestId("processing-indicator")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("processing-error")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("processing-error")).toContainText("LLM service unavailable");
  });
});
