import { test, expect, Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Felix — CV Template selection journey (PQ)
 *
 * Tests that after full CV generation:
 *  - The Design tab opens a template picker with 7 options
 *  - Selecting the 'executive' template and regenerating produces a valid CV
 *
 * PQ tier: requires the full Docker stack (LLM_PROVIDER=mock).
 * Run locally: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 *              npx playwright test --config=playwright.config.pq.ts tests/pq/felix/felix-cv-templates.spec.ts
 */

const CV_PATH = path.join(__dirname, "../../fixtures/profiles/sample_cv.pdf");
const JD_TEXT = fs.readFileSync(
  path.join(__dirname, "../../fixtures/JDs/sample_jd.txt"),
  "utf-8"
);
const API_BASE = "http://localhost:8001";

async function resetBackendState(page: Page): Promise<void> {
  await page.request.delete(`${API_BASE}/api/profile`).catch(() => {});
}

async function generateCvAndNavigateToView(page: Page): Promise<void> {
  await resetBackendState(page);
  await page.goto("/");
  await page.waitForLoadState("load");

  const uniqueJD = `${JD_TEXT}\n\n<!-- felix-template-test: ${Date.now()} -->`;
  await page.getByTestId("jd-mode-text").click();
  await page.getByTestId('jd-text-input').fill(uniqueJD);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles(CV_PATH);
  await expect(page.getByTestId("submit-button")).toBeEnabled();
  await page.getByTestId("submit-button").click();

  await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
  await expect(page.getByTestId("loading-indicator")).not.toBeVisible({ timeout: 30000 });

  await page.getByTestId("generate-cv-button").click();

  await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

  // Wait for either the photo prompt or the template selector to appear after page init
  const skipPhotoBtn = page.getByTestId("photo-prompt-skip");
  await skipPhotoBtn.waitFor({ state: "visible", timeout: 20000 }).then(() => skipPhotoBtn.click()).catch(() => {});

  // Trigger CV generation (testid is locale-independent)
  await page.getByTestId("regenerate-cv-button").click({ timeout: 20000 });
  await expect(page.getByTestId("refinement-sidebar")).toBeVisible({ timeout: 90000 });
}

test.describe("Felix — CV Template selection (PQ)", () => {
  test("Design tab opens the template picker with at least 7 options", async ({ page }) => {
    await generateCvAndNavigateToView(page);
    await page.getByTestId("sidebar-tab-design").click();
    await page.getByTestId("change-template-btn").click();
    // Template picker should list all 7 registered templates
    const templateOptions = page.getByTestId("template-option");
    await expect(templateOptions.first()).toBeVisible({ timeout: 5000 });
    expect(await templateOptions.count()).toBeGreaterThanOrEqual(7);
  });

  test("selecting executive template and regenerating succeeds", async ({ page }) => {
    await generateCvAndNavigateToView(page);
    await page.getByTestId("sidebar-tab-design").click();
    await page.getByTestId("change-template-btn").click();

    // Select the executive template
    await page.getByTestId("template-option-executive").click();
    const regenerateBtn = page.getByTestId("regenerate-cv-button");
    await expect(regenerateBtn).toBeEnabled({ timeout: 5000 });

    // Intercept the generate request to capture cv_id
    const generateResponse = page.waitForResponse(
      (resp) => resp.url().includes("/api/cv/generate") && resp.status() === 201,
      { timeout: 15000 }
    );
    await regenerateBtn.click();
    const resp = await generateResponse;
    const body = await resp.json();
    expect(body.cv_id).toBeTruthy();

    // Wait for refinement panel to show new CV is ready
    await expect(page.getByTestId("refinement-sidebar")).toBeVisible({ timeout: 90000 });
  });
});
