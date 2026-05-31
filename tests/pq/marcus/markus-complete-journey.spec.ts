// tests/e2e/pq/markus-complete-journey.spec.ts
import { test, expect, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Marcus — Complete Journey (PQ)
 *
 * Full happy path: CV upload + JD paste → gap analysis → interview (one answer) →
 * CV generation → cover letter generation.
 *
 * PQ tier: requires the full Docker stack (LLM_PROVIDER=mock).
 * Run locally: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 *              npx playwright test --config=playwright.config.pq.ts tests/pq/marcus/markus-complete-journey.spec.ts
 * Run in CI:   Trigger the "PQ Tests" workflow in GitHub Actions.
 *
 * DO NOT run this file with the standard `npx playwright test` command.
 */

const CV_PATH = path.join(__dirname, '../../fixtures/profiles/sample_cv.pdf');
const JD_TEXT = fs.readFileSync(
  path.join(__dirname, '../../fixtures/JDs/sample_jd.txt'),
  'utf-8'
);
const API_BASE = 'http://localhost:8001';

async function resetBackendState(page: Page): Promise<void> {
  await page.request.delete(`${API_BASE}/api/profile`).catch(() => {});
  // Confirm deletion before navigating — home page redirects to dashboard if profile still exists
  for (let i = 0; i < 10; i++) {
    const res = await page.request.get(`${API_BASE}/api/profile/exists`).catch(() => null);
    if (res?.ok()) {
      const data = await res.json().catch(() => ({ exists: true }));
      if (!data.exists) break;
    }
    await page.waitForTimeout(300);
  }
}

/**
 * Drives the full Marcus journey from scratch through to a ready cover letter.
 * Returns the flowId extracted from the URL.
 *
 * Journey: home → gaps → interview (1 answer, end early) → CV generation → cover letter
 */
async function setupCompleteJourney(page: Page): Promise<string> {
  await resetBackendState(page);
  await page.goto('/');
  await page.waitForLoadState('load');

  // Paste JD (unique token prevents flow-creation idempotency re-using a stale flow)
  const uniqueJD = `${JD_TEXT}\n\n<!-- markus-complete-journey: ${Date.now()} -->`;
  await page.getByTestId('jd-mode-text').click();
  await page.getByTestId('jd-text-input').fill(uniqueJD);

  // Upload CV
  await page.getByTestId('file-input').setInputFiles(CV_PATH);
  await expect(page.getByTestId('submit-button')).toBeEnabled();
  await page.getByTestId('submit-button').click();

  // Wait for gap analysis
  await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
  await expect(page.getByTestId('loading-indicator')).not.toBeVisible({
    timeout: 30000,
  });

  // Start interview
  const interviewButton = page.getByTestId('interview-button');
  await expect(interviewButton).toBeVisible({ timeout: 10000 });
  await interviewButton.click();
  await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
  await expect(page.getByTestId('interview-loading')).not.toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByTestId('interview-question')).toBeVisible();

  // Answer one question
  const firstQuestion = await page.getByTestId('interview-question').textContent();
  await page.getByTestId('answer-textarea').fill(
    'Ich habe über 10 Jahre Erfahrung in der Softwareentwicklung, davon 6 Jahre mit ' +
      'Python und FastAPI in produktiven Umgebungen. Ich habe mehrere ' +
      'Microservice-Architekturen entworfen und betrieben.'
  );
  await page.getByTestId('send-button').click();

  // Wait for question to change or completion screen to appear after the answer
  await expect(page.getByTestId('interview-question'))
    .not.toHaveText(firstQuestion ?? '', { timeout: 30000 })
    .catch((err: Error) => {
      if (!err.message.includes('Timeout')) throw err;
    });

  // End early if not already on completion screen
  const completionVisible = await page
    .getByTestId('completion-screen')
    .waitFor({ state: 'visible', timeout: 3000 })
    .then(() => true)
    .catch(() => false);
  if (!completionVisible) {
    await page.getByTestId('done-button').click();
    await expect(page.getByTestId('done-confirm')).toBeVisible();
    await page.getByRole('button', { name: /End interview/i }).click();
  }
  await expect(page.getByTestId('completion-screen')).toBeVisible({
    timeout: 30000,
  });

  // Navigate to CV page
  await page.getByTestId('generate-cv-button').click();
  await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 60000 });

  // Skip photo prompt if shown
  const skipBtn = page.getByTestId('photo-prompt-skip');
  await skipBtn.waitFor({ state: 'visible', timeout: 8000 }).then(() => skipBtn.click()).catch(() => {});

  // Generate CV (button testid is stable regardless of locale)
  await page.getByTestId('regenerate-cv-button').click({ timeout: 15000 });
  await expect(page.getByTestId('refinement-panel')).toBeVisible({
    timeout: 90000,
  });

  // Open cover letter generation modal (trigger now lives in the CV page action bar)
  await page.getByTestId('page-action-cover-letter-generate').click();
  await expect(page.getByTestId('cover-letter-modal')).toBeVisible();

  // Fill minimal inputs and generate
  await page.getByTestId('cl-salary').fill('95.000 € p.a.');
  await page.getByTestId('cl-modal-generate').click();

  // Wait for cover letter page and iframe
  await page.waitForURL(/\/flow\/.*\/cover-letter/, { timeout: 30000 });
  await expect(page.getByTestId('cover-letter-iframe')).toBeVisible({
    timeout: 90000,
  });

  // Extract and return flowId
  const url = page.url();
  const match = url.match(/\/flow\/([^/]+)\//);
  return match ? match[1] : '';
}

test.describe('Marcus — Complete Journey (PQ)', () => {
  test.setTimeout(8 * 60 * 1000); // 8 min: helper sequential timeouts sum to ~7.5 min worst-case

  test('US-MK01: complete journey ends on cover letter page with iframe visible', async ({
    page,
  }) => {
    await setupCompleteJourney(page);
    await expect(page).toHaveURL(/\/flow\/.*\/cover-letter/);
    await expect(page.getByTestId('cover-letter-iframe')).toBeVisible();
  });

  test('US-MK02: back-to-CV navigation works from cover letter page', async ({
    page,
  }) => {
    const flowId = await setupCompleteJourney(page);
    await expect(page.getByTestId('cl-view-cv-btn')).toBeVisible();
    await page.getByTestId('cl-view-cv-btn').click();
    await page.waitForURL(`**/flow/${flowId}/cv`);
  });

  test('US-MK03: PDF download button is present and enabled after full journey', async ({
    page,
  }) => {
    await setupCompleteJourney(page);
    await expect(page.getByTestId('cl-topbar-download-btn')).toBeVisible();
    await expect(page.getByTestId('cl-topbar-download-btn')).toBeEnabled();
  });
});
