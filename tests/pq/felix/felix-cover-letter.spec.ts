// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Tobias Rosenbaum

import { test, expect, Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Felix — Cover letter: generate, navigate, edit, restyle (PQ)
 *
 * Renamed from `cover-letter.spec.ts` on 2026-09-02 to satisfy this tier's
 * own naming convention, `<persona>-<journey>.spec.ts`. It stays with Felix:
 * the flow-from-scratch at the top is setup, and what it actually asserts is
 * the finetuning surface — the body section editable and saving, and the
 * design tab's template choice.
 *
 * Tests cover letter generation, navigation, editing, and templates.
 * Each test builds its own state from scratch via the full UI flow.
 *
 * PQ tier: requires the full Docker stack (LLM_PROVIDER=mock).
 * Run locally: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 *              npx playwright test --config=playwright.config.pq.ts tests/pq/felix/felix-cover-letter.spec.ts
 * Run in CI:   see docs/TESTING.md (the PQ tier runs inside the "Integration & E2E Tests" job).
 *
 * NOTE: This replaces tests/e2e/test_cover_letter.spec.ts which was silently
 * skipped on every run due to a broken beforeAll (GET /api/flow returned 404)
 * and reliance on an unset TEST_FLOW_ID env var.
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

/**
 * Drives the full flow from scratch through to a ready cover letter.
 * Returns the flowId extracted from the URL.
 *
 * Journey: home → gaps → (skip interview) → CV generation → cover letter modal → cover letter page
 */
async function setupCoverLetter(page: Page): Promise<string> {
  await resetBackendState(page);
  await page.goto("/");
  await page.waitForLoadState("load");

  const uniqueJD = `${JD_TEXT}\n\n<!-- cover-letter-test: ${Date.now()} -->`;
  await page.getByTestId("jd-mode-text").click();
  await page
    .getByTestId('jd-text-input')
    .fill(uniqueJD);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles(CV_PATH);
  await expect(page.getByTestId("submit-button")).toBeEnabled();
  await page.getByTestId("submit-button").click();

  // Wait for gaps page and advance to CV generation
  await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
  await expect(page.getByTestId("loading-indicator")).not.toBeVisible({
    timeout: 30000,
  });
  await page.getByTestId("generate-cv-button").click();

  // Wait for CV page
  await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

  // Skip photo prompt if shown (testid is locale-independent)
  // Note: isVisible() does not wait — must use waitFor to handle async phase transition
  const skipPhotoBtn = page.getByTestId("photo-prompt-skip");
  await skipPhotoBtn.waitFor({ state: "visible", timeout: 10000 }).then(() => skipPhotoBtn.click()).catch(() => {});

  // Trigger CV generation (testid is locale-independent)
  await page.getByTestId("regenerate-cv-button").click({ timeout: 15000 });

  // Wait for CV to be ready
  await expect(page.getByTestId("refinement-sidebar")).toBeVisible({
    timeout: 90000,
  });

  // Open cover letter generation modal (the generate trigger now lives in the
  // shared refinement sidebar's Aktionen tab — E038)
  await page.getByTestId("sidebar-tab-actions").click();
  await page.getByTestId("cv-actions-generate-cl").click();
  await expect(page.getByTestId("cover-letter-modal")).toBeVisible();

  // Fill minimal required inputs and generate
  await page.getByTestId("cl-salary").fill("95.000 € p.a.");
  await page.getByTestId("cl-modal-generate").click();

  // Wait for navigation to cover letter page
  await page.waitForURL(/\/flow\/.*\/cover-letter/, { timeout: 30000 });

  // Wait for the cover letter iframe to appear (generation may still be running)
  await expect(page.getByTestId("cover-letter-iframe")).toBeVisible({
    timeout: 60000,
  });

  // Extract flowId from URL
  const url = page.url();
  const match = url.match(/\/flow\/([^/]+)\//);
  return match ? match[1] : "";
}

test.describe("Felix — Cover letter: generate, navigate, edit, restyle (PQ)", () => {
  test("US-CL01: navigates to cover-letter page after generation", async ({
    page,
  }) => {
    await setupCoverLetter(page);
    await expect(page).toHaveURL(/\/flow\/.*\/cover-letter/);
    await expect(page.getByTestId("cover-letter-iframe")).toBeVisible();
  });

  test("US-CL05: CV page shows Generate Cover Letter button", async ({
    page,
  }) => {
    // Test stays on CV page — no need to navigate to cover-letter
    await resetBackendState(page);
    await page.goto("/");
    await page.waitForLoadState("load");

    const uniqueJD = `${JD_TEXT}\n\n<!-- cl-btn-test: ${Date.now()} -->`;
    await page.getByTestId("jd-mode-text").click();
    await page
      .getByTestId('jd-text-input')
      .fill(uniqueJD);
    await page.getByTestId("file-input").setInputFiles(CV_PATH);
    await page.getByTestId("submit-button").click();

    await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
    await page.getByTestId("generate-cv-button").click();
    await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

    const skipPhotoBtn = page.getByTestId("photo-prompt-skip");
    await skipPhotoBtn.waitFor({ state: "visible", timeout: 10000 }).then(() => skipPhotoBtn.click()).catch(() => {});
    await page.getByTestId("regenerate-cv-button").click({ timeout: 15000 });
    await expect(page.getByTestId("refinement-sidebar")).toBeVisible({
      timeout: 90000,
    });

    await page.getByTestId("sidebar-tab-actions").click();
    await expect(
      page.getByTestId("cv-actions-generate-cl")
    ).toBeVisible();
  });

  test("US-CL02: pre-generation modal opens and accepts inputs", async ({
    page,
  }) => {
    await resetBackendState(page);
    await page.goto("/");
    await page.waitForLoadState("load");

    const uniqueJD = `${JD_TEXT}\n\n<!-- cl-modal-test: ${Date.now()} -->`;
    await page.getByTestId("jd-mode-text").click();
    await page
      .getByTestId('jd-text-input')
      .fill(uniqueJD);
    await page.getByTestId("file-input").setInputFiles(CV_PATH);
    await page.getByTestId("submit-button").click();

    await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
    await page.getByTestId("generate-cv-button").click();
    await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

    const skipPhotoBtn = page.getByTestId("photo-prompt-skip");
    await skipPhotoBtn.waitFor({ state: "visible", timeout: 10000 }).then(() => skipPhotoBtn.click()).catch(() => {});
    await page.getByTestId("regenerate-cv-button").click({ timeout: 15000 });
    await expect(page.getByTestId("refinement-sidebar")).toBeVisible({
      timeout: 90000,
    });

    await page.getByTestId("sidebar-tab-actions").click();
    await page.getByTestId("cv-actions-generate-cl").click();

    await expect(page.getByTestId("cover-letter-modal")).toBeVisible();
    await expect(page.getByTestId("cl-recipient-name")).toBeVisible();

    await page.getByTestId("cl-salary").fill("95.000 – 110.000 € p.a.");
    await page.getByTestId("cl-availability").fill("3 Monate zum Monatsende");
    await page.getByTestId("cl-tone-formal").click();

    // Cancel — don't actually generate
    await page.getByTestId("cl-modal-cancel").click();
    await expect(page.getByTestId("cover-letter-modal")).not.toBeVisible();
  });

  test("US-CL05: cover letter page has back-to-CV navigation", async ({
    page,
  }) => {
    const flowId = await setupCoverLetter(page);
    await expect(page.getByTestId("document-nav-cv")).toBeVisible();
    await page.getByTestId("document-nav-cv").click();
    await page.waitForURL(`**/flow/${flowId}/cv`);
  });

  test("US-CL06: PDF download button is present and enabled", async ({
    page,
  }) => {
    await setupCoverLetter(page);
    await expect(page.getByTestId("document-download-btn")).toBeVisible();
    await expect(page.getByTestId("document-download-btn")).toBeEnabled();
  });

  test("US-CL07: body section is editable and saves", async ({ page }) => {
    await setupCoverLetter(page);

    await page.getByTestId("sidebar-tab-edit").click();
    const textarea = page.getByTestId("cl-body-textarea");
    await expect(textarea).toBeVisible();

    await textarea.fill(
      "Sehr geehrte Frau Dr. Müller,\n\nTestinhalt für E2E."
    );
    await page.getByTestId("cl-save-body-btn").click();

    await expect(page.getByTestId("cl-save-body-btn")).not.toBeVisible({
      timeout: 5000,
    });
  });

  test("US-CL09: design tab shows 7 template options", async ({ page }) => {
    await setupCoverLetter(page);
    await page.getByTestId("sidebar-tab-edit").click();

    const templates = [
      "classic_german",
      "modern_swiss",
      "executive",
      "tech_developer",
      "creative_sidebar",
      "academic",
      "compact_pro",
    ];
    for (const tmpl of templates) {
      await expect(page.getByTestId(`cl-template-${tmpl}`)).toBeVisible();
    }
  });

  test("US-CL10: regenerate button opens modal", async ({ page }) => {
    await setupCoverLetter(page);
    await page.getByTestId("sidebar-tab-actions").click();
    await page.getByTestId("cl-regenerate-btn").click();
    await expect(page.getByTestId("cover-letter-modal")).toBeVisible();
    await page.getByTestId("cl-modal-cancel").click();
    await expect(page.getByTestId("cover-letter-modal")).not.toBeVisible();
  });
});
