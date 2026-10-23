// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/mobile/draft-guards.spec.ts — Hazelnut build 3, WP-FF, phone lane:
// the unsent-draft guard (MD3-1) through the phone nav drawer, the letter
// form's save-to-profile checkbox inside the viewport (B3-11), and the
// first-run frame on /profile/upload (A-11). page.route stubs only.
import { ADMIN_USER, expect, test } from '../../support/auth-fixture';
import { ANSWER, FLOW_ID as INTERVIEW_FLOW, nextTurn, stubInterview } from '../interview-receipt-fixtures';
import { FLOW_ID as DOC_FLOW, CL_ID, JOB_ID, json, stubDocuments } from '../edit-tab-stub';

test.use({ authUser: { ...ADMIN_USER, ui_language: 'de' } });

test('interview: an unsent answer holds the phone nav drawer', async ({ page }) => {
  await stubInterview(page, nextTurn(null));
  await page.route('**/api/profile/enrichment-history**', (r) => json(r, []));
  await page.goto(`/flow/${INTERVIEW_FLOW}/interview`);
  await page.getByTestId('interview-intro-start').click();
  await page.getByTestId('answer-textarea').fill(ANSWER);
  await page.getByTestId('mobile-nav-hamburger').click();
  await page.getByTestId('mobile-nav-drawer').getByTestId('nav-item-dashboard').click();
  const dialog = page.getByTestId('edit-unsaved-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Antwort noch nicht gesendet');
  await expect(dialog.getByTestId('edit-unsaved-discard')).toBeInViewport();
  await page.getByTestId('edit-unsaved-stay').click();
  await expect(page.getByTestId('answer-textarea')).toHaveValue(ANSWER);
});

test('interview: a reload after Start lands on the question (F13)', async ({ page }) => {
  await stubInterview(page, nextTurn(null));
  await page.goto(`/flow/${INTERVIEW_FLOW}/interview`);
  await page.getByTestId('interview-intro-start').click();
  await page.reload();
  await expect(page.getByTestId('interview-question')).toBeVisible();
  await expect(page.getByTestId('interview-intro')).toHaveCount(0);
});

test('letter form: the checkbox and the generate button are reachable on a phone', async ({ page }) => {
  await stubDocuments(page);
  await page.route('**/api/profile', (r) => json(r, { profile: {} }));
  await page.route('**/api/cover-letter/generate', (r) => json(r, { cover_letter_id: CL_ID, status: 'pending' }));
  // A flow without a letter yet: the page offers "Anschreiben erstellen",
  // which opens the form on the phone too.
  await page.route(`**/api/flow/${DOC_FLOW}/state`, (r) =>
    json(r, {
      flow_id: DOC_FLOW,
      current_step: 'complete',
      available_actions: {},
      job_id: JOB_ID,
      application_id: null,
      job_summary: { role_title: 'Leiter Operations' },
      cv_summary: null,
      cover_letter_summary: null,
    }),
  );
  await page.goto(`/flow/${DOC_FLOW}/cover-letter`);
  await page.getByTestId('cl-not-found-generate').click();
  const modal = page.getByTestId('cover-letter-modal');
  await expect(modal).toBeVisible();
  const box = page.getByTestId('cl-save-availability');
  await box.scrollIntoViewIfNeeded();
  await expect(box).toBeChecked();
  await expect(box).toBeInViewport();
  await page.getByTestId('cl-modal-generate').scrollIntoViewIfNeeded();
  await expect(page.getByTestId('cl-modal-generate')).toBeInViewport();
});

test('/profile/upload without a profile renders the first-run screen (A-11)', async ({ page }) => {
  await page.route('**/api/profile/exists', (r) => json(r, { exists: false }));
  await page.route('**/api/profile/import-jobs**', (r) => json(r, []));
  await page.goto('/profile/upload');
  await expect(page.getByTestId('profile-update-first-run')).toBeVisible();
  await expect(page.getByText('Was hat sich geändert?')).toHaveCount(0);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
});
