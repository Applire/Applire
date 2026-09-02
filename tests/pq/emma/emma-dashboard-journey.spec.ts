// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Tobias Rosenbaum

import { test, expect, Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Emma — Returning Power User: dashboard journey (PQ)
 *
 * Re-assigned from `pq/felix/` on 2026-09-02: every step below is Emma's
 * journey, not Felix's. Emma is the RETURNING POWER USER (retention,
 * one-click parallel tailoring); Felix is the FINETUNER (section-level
 * editing against a live preview). The Quick Tailor step in particular is
 * Emma's core job-to-be-done. See `Personas/Persona-and-Channel-Model.md`.
 *
 * Validates the returning-user dashboard experience end-to-end:
 *   1. New-user onboarding: CV upload + JD → gap analysis → CV generation
 *   2. Returning user redirected to /dashboard
 *   3. Dashboard shows the completed application card (CV Ready status)
 *   4. My Documents page shows the generated CV in the table
 *   5. Quick Tailor widget is usable for a second application
 *
 * PQ tier: requires the full Docker stack (LLM_PROVIDER=mock).
 * Run locally: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 *              npx playwright test --config=playwright.config.pq.ts tests/pq/emma/emma-dashboard-journey.spec.ts
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
 * Runs the full new-user onboarding flow through CV generation.
 * Returns the flow ID extracted from the URL.
 */
async function runFullOnboardingFlow(page: Page): Promise<string> {
  await resetBackendState(page);
  await page.goto("/");
  await page.waitForLoadState("load");

  // Use Paste Text mode so we can inject a unique JD and avoid flow dedup
  const uniqueJD = `${JD_TEXT}\n\n<!-- felix-dashboard-test: ${Date.now()} -->`;
  await page.getByTestId("jd-mode-text").click();
  await page
    .getByTestId('jd-text-input')
    .fill(uniqueJD);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles(CV_PATH);
  await expect(page.getByTestId("submit-button")).toBeEnabled();
  await page.getByTestId("submit-button").click();

  // Wait for gap analysis to complete
  await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
  await expect(page.getByTestId("loading-indicator")).not.toBeVisible({
    timeout: 30000,
  });

  // Advance directly to CV generation
  await page.getByTestId("generate-cv-button").click();

  // Wait for CV page
  await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

  const flowId = page.url().match(/\/flow\/([^/]+)\//)?.[1] ?? "";

  // Dismiss photo prompt if present (testid is locale-independent)
  // Note: isVisible() does not wait — must use waitFor to handle async phase transition
  const skipPhotoBtn = page.getByTestId("photo-prompt-skip");
  await skipPhotoBtn.waitFor({ state: "visible", timeout: 10000 }).then(() => skipPhotoBtn.click()).catch(() => {});

  // Trigger CV generation (testid is locale-independent)
  await page.getByTestId("regenerate-cv-button").click({ timeout: 15000 });

  // Wait for generation to complete
  await expect(page.getByTestId("refinement-sidebar")).toBeVisible({
    timeout: 90000,
  });

  return flowId;
}

// ────────────────────────────────────────────────────────────────────────────

test.describe("Emma — Returning Power User: dashboard journey (PQ)", () => {
  test("returning user is redirected from / to /dashboard", async ({ page }) => {
    await runFullOnboardingFlow(page);

    // Navigate back to root — must redirect to dashboard for returning user
    await page.goto("/");
    await page.waitForURL("**/dashboard", { timeout: 10000 });
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test("dashboard sidebar renders all five nav items", async ({ page }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/dashboard");

    // Scope to the sidebar nav landmark — the topbar also has an "Open settings"
    // button, which would make an unscoped /settings/i match ambiguous.
    const nav = page.getByRole("navigation");
    await expect(nav.getByRole("button", { name: /dashboard/i })).toBeVisible();
    await expect(nav.getByRole("button", { name: /profile|masterprofil/i }).first()).toBeVisible();
    await expect(nav.getByRole("button", { name: /profil aktualisieren|update profile/i })).toBeVisible();
    await expect(nav.getByRole("button", { name: /documents|dokumente/i })).toBeVisible();
    await expect(nav.getByRole("button", { name: /settings|einstellungen/i })).toBeVisible();
  });

  test("dashboard shows the completed application card with CV Ready status", async ({ page }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/dashboard");

    // Application card grid must be present
    await expect(page.getByText(/CV Ready/i)).toBeVisible({ timeout: 10000 });

    // Open button is available for the ready CV
    await expect(page.getByTestId("dashboard-card-open-btn").first()).toBeVisible();
  });

  test("Open button on dashboard card navigates to the CV view (via the dossier)", async ({ page }) => {
    // E041/US235 — the dashboard card's Open button now opens the per-application
    // dossier (like the card body); the dossier header's Open CV action is where
    // Emma actually reaches the CV. No capability is lost — same intent, one hop.
    await runFullOnboardingFlow(page);
    await page.goto("/dashboard");
    await expect(page.getByTestId("dashboard-card-open-btn").first()).toBeVisible({
      timeout: 10000,
    });

    await page.getByTestId("dashboard-card-open-btn").first().click();
    await expect(page).toHaveURL(/\/applications\/.+/, { timeout: 10000 });

    await page.getByTestId("dossier-open-cv").click();
    await expect(page).toHaveURL(/\/flow\/.+\/cv/, { timeout: 10000 });
  });

  test("My Documents page shows the generated CV in the table", async ({ page }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/documents");

    // Stats strip should show at least 1 document
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 10000 });

    // Open button for the ready CV exists
    await expect(page.getByTestId("documents-table-open-btn").first()).toBeVisible();
  });

  test("Documents table Open button navigates to the CV view", async ({ page }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/documents");

    await expect(page.getByTestId("documents-table-open-btn").first()).toBeVisible({
      timeout: 10000,
    });

    await page.getByTestId("documents-table-open-btn").first().click();
    await expect(page).toHaveURL(/\/flow\/.+\/cv/, { timeout: 10000 });
  });

  test("Quick Tailor widget accepts a second JD text and starts a new flow", async ({ page }) => {
    await runFullOnboardingFlow(page);
    await page.goto("/dashboard");

    // Switch to Paste Text tab
    await page.getByText(/Paste Text|Text einfügen/i).click();
    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeVisible();

    const secondJD = `${JD_TEXT}\n\n<!-- second-job: ${Date.now()} -->`;
    await textarea.fill(secondJD);

    // Analyse button must become enabled
    const analyseBtn = page.getByRole("button", { name: /analysieren|analyse/i });
    await expect(analyseBtn).toBeEnabled({ timeout: 3000 });

    // A new flow starts via the flow index, which advances the state machine.
    // Emma already has a profile, so the flow skips cv_import and lands on
    // the gap analysis step.
    await analyseBtn.click();
    await expect(page).toHaveURL(/\/flow\/.+\/gaps/, { timeout: 60000 });
  });
});
