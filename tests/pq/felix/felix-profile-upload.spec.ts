// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024 Tobias Rosenbaum

import { test, expect, Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Felix — Returning User: Profile Upload (PQ, Sprint 32)
 *
 * Validates that a returning user (with an existing profile) can navigate
 * to the "Profil aktualisieren" page via the sidebar, upload an updated CV,
 * and land back at /profile after successful processing.
 *
 * PQ tier: requires the full Docker stack (LLM_PROVIDER=mock).
 * Run locally: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 *              npx playwright test --config=playwright.config.pq.ts tests/pq/felix/felix-profile-upload.spec.ts
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

/** Full new-user onboarding through CV generation — establishes a returning user session. */
async function runFullOnboardingFlow(page: Page): Promise<void> {
  await resetBackendState(page);
  await page.goto("/");
  await page.waitForLoadState("load");

  const uniqueJD = `${JD_TEXT}\n\n<!-- felix-upload-test: ${Date.now()} -->`;
  await page.getByTestId("jd-mode-text").click();
  await page
    .getByTestId('jd-text-input')
    .fill(uniqueJD);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles(CV_PATH);
  await expect(page.getByTestId("submit-button")).toBeEnabled();
  await page.getByTestId("submit-button").click();

  await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
  await expect(page.getByTestId("loading-indicator")).not.toBeVisible({
    timeout: 30000,
  });

  await page.getByTestId("generate-cv-button").click();
  await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

  const skipPhotoBtn = page.getByTestId("photo-prompt-skip");
  await skipPhotoBtn
    .waitFor({ state: "visible", timeout: 10000 })
    .then(() => skipPhotoBtn.click())
    .catch(() => {});

  await page.getByTestId("regenerate-cv-button").click({ timeout: 15000 });
  await expect(page.getByTestId("refinement-sidebar")).toBeVisible({
    timeout: 90000,
  });
}

// ────────────────────────────────────────────────────────────────────────────

test.describe("Felix — Returning User: Profile Upload (Sprint 32 PQ)", () => {
  test("sidebar 'Profil aktualisieren' navigates to /profile/upload", async ({
    page,
  }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/dashboard");

    await page
      .getByRole("button", { name: /profil aktualisieren|update profile/i })
      .click();

    await expect(page).toHaveURL(/\/profile\/upload/, { timeout: 10000 });
  });

  test("returning user can upload an updated CV via /profile/upload", async ({
    page,
  }) => {
    await runFullOnboardingFlow(page);
    // ProfileUpdateChooser is shown at /profile/upload; the import view renders
    // only when action=upload is set (matching the chooser's own link target).
    await page.goto("/profile/upload?action=upload");

    // Hidden file input accepts the updated CV
    const fileInput = page.getByTestId("main-file-input");
    await fileInput.setInputFiles(CV_PATH);

    // Upload button becomes active
    const uploadBtn = page.getByTestId("main-upload-button");
    await expect(uploadBtn).toBeEnabled({ timeout: 5000 });
    await uploadBtn.click();

    // Success strip must appear
    await expect(page.getByTestId("upload-success-strip")).toBeVisible({
      timeout: 60000,
    });

    // After success the user is redirected to /profile
    await expect(page).toHaveURL(/\/profile/, { timeout: 15000 });
  });

  test("upload history panel shows previous uploads on /profile/upload", async ({
    page,
  }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/profile/upload?action=upload");

    // History panel must exist
    await expect(page.getByTestId("upload-history-panel")).toBeVisible({
      timeout: 10000,
    });

    // The CV uploaded during onboarding must appear in the list
    await expect(
      page.getByTestId("upload-history-panel").locator("li").first()
    ).toBeVisible({ timeout: 10000 });
  });
});
