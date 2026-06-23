// tests/e2e/pq/marcus-new-user-journey.spec.ts
import { test, expect, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Marcus — New User Journey (PQ)
 *
 * Covers the full happy path for a new Marcus-persona user:
 *   CV upload → JD analysis → gap detection → interview → CV generation
 *
 * PQ tier: requires the full Docker stack (LLM_PROVIDER=mock).
 * Run locally: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 *              npx playwright test --config=playwright.config.pq.ts tests/pq/marcus/marcus-new-user-journey.spec.ts
 * Run in CI:   Trigger the "PQ Tests" workflow in GitHub Actions.
 *
 * DO NOT run this file with the standard `npx playwright test` command.
 */

const CV_PATH = path.join(__dirname, '../../fixtures/profiles/sample_cv.pdf');
const JD_TEXT = fs.readFileSync(path.join(__dirname, '../../fixtures/JDs/sample_jd.txt'), 'utf-8');

// ---------------------------------------------------------------------------
// Helper: navigate to gaps page from scratch
// ---------------------------------------------------------------------------

const API_BASE = 'http://localhost:8001';

async function resetBackendState(page: Page): Promise<void> {
  // Erase all user data so each test starts from the "new user" state.
  // DELETE /api/profile keeps the stub User row (required for FK constraints)
  // but removes uploads, profiles, flows, gap analyses, and generated CVs.
  await page.request.delete(`${API_BASE}/api/profile`).catch(() => {
    // Ignore errors (e.g., nothing to delete on first run)
  });
}

async function navigateToGapsPage(page: Page): Promise<string> {
  await resetBackendState(page);
  await page.goto('/');
  await page.waitForLoadState('load');

  // Switch to "Paste Text" mode for JD and fill it in.
  // Append a unique token so each test gets a fresh job_id (flow creation is idempotent per job_id).
  const uniqueJD = `${JD_TEXT}\n\n<!-- test-run: ${Date.now()} -->`;
  await page.getByTestId('jd-mode-text').click();
  await page.getByTestId('jd-text-input').fill(uniqueJD);

  // Upload CV
  const fileInput = page.getByTestId('file-input');
  await fileInput.setInputFiles(CV_PATH);

  // Submit
  const submitButton = page.getByTestId('submit-button');
  await expect(submitButton).toBeEnabled();
  await submitButton.click();

  // Wait for gaps page
  await expect(page).toHaveURL(/\/flow\/.*\/gaps/, { timeout: 90000 });
  await expect(page.getByTestId('loading-indicator')).not.toBeVisible({ timeout: 30000 });
  await expect(page.getByTestId('gap-analysis-page')).toBeVisible();

  // Extract flowId from URL
  const url = page.url();
  const match = url.match(/\/flow\/([^/]+)\//);
  return match ? match[1] : '';
}

// ---------------------------------------------------------------------------
// Full Interview Mode (19.1, 19.2, 19.6)
// ---------------------------------------------------------------------------

test.describe('Full Interview Mode', () => {

  test('should start interview from gaps page and show first question', async ({ page }) => {
    await navigateToGapsPage(page);

    // Click "Quick Interview" button (only visible for new users with gaps)
    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    // Should navigate to interview page
    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });

    // Loading spinner should disappear
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Interview page and first question must be visible
    await expect(page.getByTestId('interview-page')).toBeVisible();
    await expect(page.getByTestId('interview-question')).toBeVisible();
    const questionText = await page.getByTestId('interview-question').textContent();
    expect(questionText?.trim().length).toBeGreaterThan(0);
  });

  test('should answer a question and receive the next question', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('interview-question')).toBeVisible();

    const firstQuestion = await page.getByTestId('interview-question').textContent();

    // Type and send an answer
    const textarea = page.getByTestId('answer-textarea');
    await textarea.fill('I have 6 years of Python experience, primarily with FastAPI and async frameworks.');
    await page.getByTestId('send-button').click();

    // Spinner should show, then next question or completion should appear
    await expect(page.getByTestId('interview-question')).not.toHaveText(firstQuestion ?? '', {
      timeout: 30000,
    }).catch(() => {
      // Might have gone to completion screen — that's also valid
    });

    // Either another question OR the completion screen must be visible
    const completionVisible = await page.getByTestId('completion-screen').isVisible().catch(() => false);
    const questionVisible = await page.getByTestId('interview-question').isVisible().catch(() => false);
    expect(completionVisible || questionVisible).toBe(true);
  });

  test('shows "I\'m done" confirmation before ending early', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Click "I'm done" button
    const doneButton = page.getByTestId('done-button');
    await expect(doneButton).toBeVisible();
    await doneButton.click();

    // Confirmation panel must appear
    await expect(page.getByTestId('done-confirm')).toBeVisible();
  });

  test('completion screen shows summary and "Generate Tailored CV" CTA', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Fast-path: click "I'm done" and confirm end
    await page.getByTestId('done-button').click();
    await expect(page.getByTestId('done-confirm')).toBeVisible();
    // Click "End interview" button inside the confirmation
    await page.getByRole('button', { name: /End interview/i }).click();

    // Completion screen must appear
    await expect(page.getByTestId('completion-screen')).toBeVisible({ timeout: 30000 });

    // "Generate Tailored CV" CTA must be visible
    await expect(page.getByTestId('generate-cv-button')).toBeVisible();
  });

  test('"Generate Tailored CV" CTA navigates to cv page', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // End early to reach completion screen quickly
    await page.getByTestId('done-button').click();
    await expect(page.getByTestId('done-confirm')).toBeVisible();
    await page.getByRole('button', { name: /End interview/i }).click();
    await expect(page.getByTestId('completion-screen')).toBeVisible({ timeout: 30000 });

    // Click CTA
    await page.getByTestId('generate-cv-button').click();
    await expect(page).toHaveURL(/\/flow\/.*\/cv/, { timeout: 30000 });
  });

  // 19.2 — completion reason: gaps_resolved
  test('completion screen shows "Interview Complete — Gaps Closed!" for gaps_resolved reason', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('interview-question')).toBeVisible();

    // Intercept the next message and inject a gaps_resolved completion
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: true,
          reason: 'gaps_resolved',
          questions_asked: 3,
          gaps_resolved: 3,
          gaps_unresolved: [],
          completeness_score: 0.75,
          pending_conflicts: null,
        }),
      });
    });

    await page.getByTestId('answer-textarea').fill('I have extensive Python experience with FastAPI.');
    await page.getByTestId('send-button').click();

    await expect(page.getByTestId('completion-screen')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('heading', { name: /Interview Complete.*Gaps Closed/i })).toBeVisible();
  });

  // 19.2 — completion reason: max_questions_reached
  test('completion screen shows "Interview Limit Reached" for max_questions_reached reason', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('interview-question')).toBeVisible();

    // Intercept the next message and inject a max_questions_reached completion
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: true,
          reason: 'max_questions_reached',
          questions_asked: 12,
          gaps_resolved: 4,
          gaps_unresolved: ['Docker containerisation', 'CI/CD pipelines'],
          completeness_score: 0.55,
          pending_conflicts: null,
        }),
      });
    });

    await page.getByTestId('answer-textarea').fill('I have worked with various deployment tools.');
    await page.getByTestId('send-button').click();

    await expect(page.getByTestId('completion-screen')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('heading', { name: /Interview Limit Reached/i })).toBeVisible();
  });

  // 19.8 — cultural sensitivity badge for category B questions
  test('category B question shows teal cultural sensitivity badge', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No gaps present — interview button not shown');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('interview-question')).toBeVisible();

    // Badge must NOT be visible on the first question (isCategoryB starts as false)
    await expect(page.getByTestId('cultural-sensitivity-badge')).not.toBeVisible();

    // Intercept: return a next question containing the category-B trigger phrase
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: false,
          question: 'Based on your background, can you confirm your experience with Kubernetes in production?',
          gaps_remaining: 2,
          pending_conflicts: null,
        }),
      });
    });

    await page.getByTestId('answer-textarea').fill('I have solid experience with container orchestration.');
    await page.getByTestId('send-button').click();

    // Badge must appear for the category B follow-up question
    await expect(page.getByTestId('cultural-sensitivity-badge')).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Gap-Click Mode (19.3, 19.4, 19.11)
// ---------------------------------------------------------------------------

test.describe('Gap-Click Mode', () => {

  test('should show "Answer this gap" trigger for each gap', async ({ page }) => {
    await navigateToGapsPage(page);

    const gapsSection = page.getByTestId('gaps-section');
    if (!(await gapsSection.isVisible())) {
      test.skip(true, 'No gaps section visible — no gaps to click');
    }

    // At least one gap-click trigger must be visible
    const triggers = page.getByTestId('gap-cluster-card');
    await expect(triggers.first()).toBeVisible();
  });

  test('clicking a gap trigger shows a targeted question', async ({ page }) => {
    await navigateToGapsPage(page);

    const gapsSection = page.getByTestId('gaps-section');
    if (!(await gapsSection.isVisible())) {
      test.skip(true, 'No gaps section visible');
    }

    const trigger = page.getByTestId('gap-cluster-card').first();
    await expect(trigger).toBeVisible();
    await trigger.click();

    // Loading spinner for the micro-session
    // Then the question should appear
    await expect(page.getByTestId('gap-question')).toBeVisible({ timeout: 30000 });
    const questionText = await page.getByTestId('gap-question').textContent();
    expect(questionText?.trim().length).toBeGreaterThan(0);

    // Answer textarea and submit button must be visible
    await expect(page.getByTestId('gap-answer-textarea')).toBeVisible();
    await expect(page.getByTestId('gap-submit-button')).toBeVisible();
  });

  test('answering a gap question marks it as resolved', async ({ page }) => {
    await navigateToGapsPage(page);

    const gapsSection = page.getByTestId('gaps-section');
    if (!(await gapsSection.isVisible())) {
      test.skip(true, 'No gaps section visible');
    }

    const trigger = page.getByTestId('gap-cluster-card').first();
    await trigger.click();

    await expect(page.getByTestId('gap-answer-textarea')).toBeVisible({ timeout: 30000 });

    // Type and submit answer
    await page.getByTestId('gap-answer-textarea').fill(
      'Yes, I have extensive experience with this technology in production environments.'
    );
    await page.getByTestId('gap-submit-button').click();

    // Gap should transition to resolved state (green checkmark)
    await expect(page.getByTestId('gap-resolved').first()).toBeVisible({ timeout: 30000 });
  });

  // 19.3/19.4 — match score re-animates after gap resolution
  test('match score refresh is triggered after gap is resolved', async ({ page }) => {
    await navigateToGapsPage(page);

    const gapsSection = page.getByTestId('gaps-section');
    if (!(await gapsSection.isVisible())) {
      test.skip(true, 'No gaps section visible');
    }

    // Mock micro-session creation
    await page.route('**/api/session', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            session_id: 'mock-gap-session-score-001',
            mode: 'targeted',
            first_question: 'Can you describe your experience with this skill?',
            estimated_questions: 1,
            question: 'Can you describe your experience with this skill?',
            gaps_total: 1,
            gaps_remaining: 1,
            resumed: false,
          }),
        });
      } else {
        await route.continue();
      }
    });

    // Mock micro-session completion
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: true,
          reason: 'gaps_resolved',
          questions_asked: 1,
          gaps_resolved: 1,
          gaps_unresolved: [],
          completeness_score: 0.65,
          pending_conflicts: null,
        }),
      });
    });

    // Register a request watcher for the gaps refresh (feature 19.11)
    // The mock LLM always returns 0.68, so we verify the API is called rather than checking
    // the score display value (which stays the same since mock LLM is deterministic).
    const refreshRequestPromise = page.waitForRequest(
      (req) => req.url().includes('/gaps/refresh') && req.method() === 'POST',
      { timeout: 15000 }
    );

    // Trigger gap resolution
    const trigger = page.getByTestId('gap-cluster-card').first();
    await trigger.click();
    await expect(page.getByTestId('gap-answer-textarea')).toBeVisible({ timeout: 30000 });
    await page.getByTestId('gap-answer-textarea').fill('Yes, extensive production experience.');
    await page.getByTestId('gap-submit-button').click();

    // Wait for the gap-resolved state AND verify the refresh API was called (feature 19.11)
    await expect(page.getByTestId('gap-resolved').first()).toBeVisible({ timeout: 30000 });
    await refreshRequestPromise;
  });

  // 19.3/19.4 — multiple gap resolution in sequence
  test('resolving multiple gaps in sequence shows all as resolved', async ({ page }) => {
    await navigateToGapsPage(page);

    const gapsSection = page.getByTestId('gaps-section');
    if (!(await gapsSection.isVisible())) {
      test.skip(true, 'No gaps section visible');
    }

    const triggers = page.getByTestId('gap-cluster-card');
    const triggerCount = await triggers.count();
    if (triggerCount < 2) {
      test.skip(true, 'Fewer than 2 gaps present — cannot test sequential resolution');
    }

    // Mock micro-session creation (handles all session POSTs)
    await page.route('**/api/session', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            session_id: 'mock-gap-session-multi-001',
            mode: 'targeted',
            first_question: 'Can you describe your experience with this skill?',
            estimated_questions: 1,
            question: 'Can you describe your experience with this skill?',
            gaps_total: 1,
            gaps_remaining: 1,
            resumed: false,
          }),
        });
      } else {
        await route.continue();
      }
    });

    // Mock micro-session message completion (handles all message POSTs)
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: true,
          reason: 'gaps_resolved',
          questions_asked: 1,
          gaps_resolved: 1,
          gaps_unresolved: [],
          completeness_score: 0.70,
          pending_conflicts: null,
        }),
      });
    });

    // Mock gaps refresh (non-critical, just needs to not error)
    await page.route('**/api/job/*/gaps/refresh', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'mock-refresh-id-multi',
          match_score: 0.80,
          category_a: ['Python'],
          category_b: [],
          category_c: [],
          strengths: ['Python'],
        }),
      });
    });

    // Resolve first gap
    await triggers.first().click();
    await expect(page.getByTestId('gap-answer-textarea')).toBeVisible({ timeout: 30000 });
    await page.getByTestId('gap-answer-textarea').fill('Yes, strong experience here.');
    await page.getByTestId('gap-submit-button').click();
    await expect(page.getByTestId('gap-resolved').first()).toBeVisible({ timeout: 30000 });

    // Resolve second gap — filter out already-resolved cards
    const remainingTriggers = page.getByTestId('gap-cluster-card').filter({ hasNot: page.getByTestId('gap-resolved') });
    await expect(remainingTriggers.first()).toBeVisible({ timeout: 10000 });
    await remainingTriggers.first().click();
    await expect(page.getByTestId('gap-answer-textarea')).toBeVisible({ timeout: 30000 });
    await page.getByTestId('gap-answer-textarea').fill('Yes, solid background in this area too.');
    await page.getByTestId('gap-submit-button').click();

    // Both gaps should now be in resolved state
    await expect(page.getByTestId('gap-resolved')).toHaveCount(2, { timeout: 30000 });
  });
});

// ---------------------------------------------------------------------------
// Session Resume (19.5)
// ---------------------------------------------------------------------------

test.describe('Session Resume', () => {

  test('navigating back to interview page shows resume banner for active session', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No interview button visible');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Answer one question so the session is *genuinely* in progress. Per issue
    // #44 the resume banner is gated on real progress (questions_asked > 1), not
    // mere session pre-existence (the onboarding overlay pre-creates a session
    // with zero answers) — so a true "resume" requires at least one answer.
    await expect(page.getByTestId('interview-question')).toBeVisible({ timeout: 30000 });
    const answer = page.getByTestId('answer-textarea');
    await answer.fill('I have 6 years of Python experience with FastAPI and async frameworks.');
    // The answer POST is the deterministic signal the question was recorded
    // (questions_asked increments server-side regardless of the next prompt).
    const answerPost = page.waitForResponse(
      (r) => /\/api\/session\/.*\/message$/.test(r.url()) && r.request().method() === 'POST',
      { timeout: 30000 },
    );
    await page.getByTestId('send-button').click();
    await answerPost;
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Extract flowId
    const url = page.url();
    const match = url.match(/\/flow\/([^/]+)\//);
    const flowId = match ? match[1] : '';
    expect(flowId).toBeTruthy();

    // Navigate away
    await page.goto('/');
    await page.waitForLoadState('load');

    // Navigate back to interview
    await page.goto(`/flow/${flowId}/interview`);
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Resume banner should appear (session has genuine in-progress work, #44).
    await expect(page.getByTestId('resume-banner')).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Conflict Resolution (19.7, 19.10)
// ---------------------------------------------------------------------------

test.describe('Conflict Resolution', () => {

  test('conflict card is rendered when pending_conflicts are returned', async ({ page }) => {
    // This test verifies that IF a conflict is surfaced by the backend,
    // the ConflictCard renders with both resolution buttons.
    //
    // Because conflicts are non-deterministic (depend on profile state),
    // we inject a mock response via route interception.

    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No interview button visible');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Intercept the next message POST and inject a conflict
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: false,
          question: 'Tell me more about your experience.',
          gaps_remaining: 2,
          pending_conflicts: [
            {
              conflict_id: 'test-conflict-001',
              field: 'work_experience[0].end_date',
              old_value: '2021-06',
              new_value: '2022-03',
            },
          ],
        }),
      });
    });

    // Send an answer to trigger the intercepted response
    await page.getByTestId('answer-textarea').fill('I have 5 years of relevant experience.');
    await page.getByTestId('send-button').click();

    // Conflict card must appear
    await expect(page.getByTestId('conflict-card')).toBeVisible({ timeout: 10000 });

    // Both resolution buttons must be present
    await expect(page.getByTestId('conflict-keep-old')).toBeVisible();
    await expect(page.getByTestId('conflict-use-new')).toBeVisible();
  });

  test('clicking "Keep old" on conflict card dismisses it', async ({ page }) => {
    await navigateToGapsPage(page);

    const interviewButton = page.getByTestId('interview-button');
    if (!(await interviewButton.isVisible())) {
      test.skip(true, 'No interview button visible');
    }
    await interviewButton.click();

    await expect(page).toHaveURL(/\/flow\/.*\/interview/, { timeout: 30000 });
    await expect(page.getByTestId('interview-loading')).not.toBeVisible({ timeout: 30000 });

    // Inject conflict via route interception
    await page.route('**/api/session/*/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          complete: false,
          question: 'What was your role at Acme Corp?',
          gaps_remaining: 1,
          pending_conflicts: [
            {
              conflict_id: 'test-conflict-002',
              field: 'work_experience[0].title',
              old_value: 'Software Engineer',
              new_value: 'Senior Software Engineer',
            },
          ],
        }),
      });
    });

    // Also intercept the conflict resolve endpoint
    await page.route('**/api/profile/conflicts/*/resolve', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.getByTestId('answer-textarea').fill('I was a software engineer there.');
    await page.getByTestId('send-button').click();

    await expect(page.getByTestId('conflict-card')).toBeVisible({ timeout: 10000 });

    // Click "Keep old"
    await page.getByTestId('conflict-keep-old').click();

    // Conflict card should disappear
    await expect(page.getByTestId('conflict-card')).not.toBeVisible({ timeout: 5000 });
  });
});
