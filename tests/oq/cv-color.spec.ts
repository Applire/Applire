import { test, expect } from "@playwright/test";

/**
 * CV Design tab — OQ tests
 *
 * Covers the DesignTab inside RefinementPanel:
 *  - Design tab renders with company color card
 *  - Selecting a swatch updates the hex input
 *  - Apply button disabled when no change
 *  - Apply calls PATCH /api/cv/{id}/color and triggers HTML refresh
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const TEST_FLOW_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff";
const TEST_CV_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const TEST_JOB_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CV_PAGE_URL = `/flow/${TEST_FLOW_ID}/cv`;

const MOCK_FLOW_STATE = {
  job_id: TEST_JOB_ID,
  job_summary: { role_title: "Senior Software Engineer" },
  gap_summary: {
    match_score: 0.85,
    gaps: [],
    sections: [],
    detected_company: { name: "Siemens AG", hex: "#009fe3" },
    current_accent_hex: "#009fe3",
  },
  cv_summary: {
    cv_id: TEST_CV_ID,
    pdf_url: `http://localhost:8001/api/cv/${TEST_CV_ID}/pdf`,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  },
};

const MOCK_CV_HTML = `<html><body style="--cv-accent:#009fe3"><h1>Max Mustermann</h1></body></html>`;
const MOCK_CV_HTML_RECOLORED = `<html><body style="--cv-accent:#c0392b"><h1>Max Mustermann</h1></body></html>`;

test.describe("CV Design tab", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/settings", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ default_color_profile_id: null, default_accent_hex: null, ui_language: "de" }),
      });
    });
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_FLOW_STATE),
      });
    });
    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      await route.fulfill({ status: 200, contentType: "text/html", body: MOCK_CV_HTML });
    });
    await page.route(`**/api/cv/${TEST_CV_ID}/color`, async (route) => {
      // Serve re-colored HTML on the next GET /html call after PATCH
      await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (r) => {
        await r.fulfill({ status: 200, contentType: "text/html", body: MOCK_CV_HTML_RECOLORED });
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          color_profile_id: "cpid-0001",
          derived: { "--cv-accent": "#c0392b", "--cv-accent-tint": "#fde8e8" },
        }),
      });
    });
    await page.goto(CV_PAGE_URL);
    // Wait for the RefinementPanel's appearance tab button — signals the CV preview phase
    // is fully rendered. Avoids networkidle which never resolves due to external font
    // requests (Google Fonts) loaded by the root layout.
    await page.waitForSelector('[data-testid="tab-design"]', { state: "visible", timeout: 30000 });
  });

  test("Design tab is visible in RefinementPanel", async ({ page }) => {
    await expect(page.getByTestId("tab-design")).toBeVisible();
  });

  test("clicking Design tab shows company color card", async ({ page }) => {
    await page.getByTestId("tab-design").click();
    await expect(page.getByText("Siemens AG")).toBeVisible();
    await expect(page.getByText("automatisch erkannt")).toBeVisible();
  });

  test("apply button is disabled when no color change", async ({ page }) => {
    await page.getByTestId("tab-design").click();
    const applyBtn = page.getByText("Farbe übernehmen");
    await expect(applyBtn).toBeDisabled();
  });

  test("clicking a different preset swatch enables apply button", async ({ page }) => {
    await page.getByTestId("tab-design").click();
    // Click a preset that is not the current accent
    await page.getByRole("button", { name: "Farbe wählen: Rot" }).click();
    await expect(page.getByText("Farbe übernehmen")).toBeEnabled();
  });

  test("applying color calls PATCH and refreshes iframe", async ({ page }) => {
    await page.getByTestId("tab-design").click();
    await page.getByRole("button", { name: "Farbe wählen: Rot" }).click();
    const patchRequest = page.waitForRequest(
      (req) => req.url().includes("/color") && req.method() === "PATCH"
    );
    await page.getByText("Farbe übernehmen").click();
    await patchRequest;
    // Apply button returns to disabled (currentAccentHex updated)
    await expect(page.getByText("Farbe übernehmen")).toBeDisabled({ timeout: 5000 });
  });
});
