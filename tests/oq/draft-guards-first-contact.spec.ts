// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/draft-guards-first-contact.spec.ts — Hazelnut build 3, WP-FF (fix
// round after the E2E tier), driven in a real browser against page.route stubs
// (no backend, no provider call):
//
//  * MD3-1 / adversarial A-5 — an unsent interview answer and the letter form's
//    typed inputs are drafts: the shell nav (in-app dialog), the browser back
//    button (dialog) and a reload / tab close (beforeunload) ask first — the
//    section editor's guard (tests/oq/edit-tab.spec.ts, adv-review 11).
//  * F13 (delivery run) — a reload after "Erste Frage zeigen" lands on the
//    current question, not on the intro.
//  * B3-11 / MD3-4 — the letter form's "Auch im Profil speichern" (default ON)
//    is sent explicitly; the server stub applies WP-FL's write rule (stripped
//    text, case-insensitive differ, empty writes nothing), so the next form
//    pre-fills what was saved.
//  * A-11 — a profile-less user on /profile/upload (the sidebar's "Lebenslauf
//    oder Stelle hinzufügen") sees the first-run screen, never "Was hat sich
//    geändert?".
import type { Page } from '@playwright/test';
import { ADMIN_USER, expect, test } from '../support/auth-fixture';
import { ANSWER, FLOW_ID as INTERVIEW_FLOW, stubInterview, nextTurn } from './interview-receipt-fixtures';
import { FLOW_ID as DOC_FLOW, CL_ID, json, stubDocuments } from './edit-tab-stub';

test.use({ authUser: { ...ADMIN_USER, ui_language: 'de' } });

const INTERVIEW = `/flow/${INTERVIEW_FLOW}/interview`;
const LETTER = `/flow/${DOC_FLOW}/cover-letter`;

/** Wait until some unload guard holds the draft (the hook arms one render later). */
async function unloadGuardArmed(page: Page, armed = true) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const e = new Event('beforeunload', { cancelable: true });
        window.dispatchEvent(e);
        return e.defaultPrevented;
      }),
    )
    .toBe(armed);
}

async function openInterview(page: Page) {
  await stubInterview(page, nextTurn(null));
  await page.route('**/api/profile/enrichment-history**', (r) => json(r, []));
  await page.route('**/api/settings', (r) => json(r, { ui_language: 'de', ui_language_explicit: true }));
  await page.goto(INTERVIEW);
}

test.describe('MD3-1 — an unsent interview answer is held', () => {
  test('the shell nav asks; stay keeps the answer, discard follows the link', async ({ page }) => {
    await openInterview(page);
    await page.getByTestId('interview-intro-start').click();
    await page.getByTestId('answer-textarea').fill(ANSWER);
    await page.getByTestId('sidebar-nav-dashboard').click();
    const dialog = page.getByTestId('edit-unsaved-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('Antwort noch nicht gesendet');
    await expect(page).toHaveURL(new RegExp(`${INTERVIEW}$`));
    await page.getByTestId('edit-unsaved-stay').click();
    await expect(page.getByTestId('answer-textarea')).toHaveValue(ANSWER);
    await page.getByTestId('sidebar-nav-dashboard').click();
    await page.getByTestId('edit-unsaved-discard').click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('the browser back button asks; stay keeps the answer and the page', async ({ page }) => {
    await page.route('**/api/profile/exists', (r) => json(r, { exists: true }));
    await page.goto('/settings');
    await openInterview(page);
    await page.getByTestId('interview-intro-start').click();
    await page.getByTestId('answer-textarea').fill(ANSWER);
    await unloadGuardArmed(page);
    await page.goBack();
    await expect(page.getByTestId('edit-unsaved-dialog')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${INTERVIEW}$`));
    await page.getByTestId('edit-unsaved-stay').click();
    await expect(page.getByTestId('answer-textarea')).toHaveValue(ANSWER);
    await page.goBack();
    await page.getByTestId('edit-unsaved-discard').click();
    await expect(page).toHaveURL(/\/settings$/);
  });

  test('a reload / tab close with an answer raises the browser prompt', async ({ page }) => {
    await openInterview(page);
    await page.getByTestId('interview-intro-start').click();
    await page.getByTestId('answer-textarea').fill(ANSWER);
    await unloadGuardArmed(page);
    const dialog = page.waitForEvent('dialog');
    await page.close({ runBeforeUnload: true });
    const d = await dialog;
    expect(d.type()).toBe('beforeunload');
    await d.dismiss();
  });

  test('no answer, no question: the shell nav and a reload pass', async ({ page }) => {
    await openInterview(page);
    await page.getByTestId('interview-intro-start').click();
    await unloadGuardArmed(page, false);
    await page.getByTestId('sidebar-nav-dashboard').click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByTestId('edit-unsaved-dialog')).toHaveCount(0);
  });

  test('a sent answer is no draft: one back press leaves the page', async ({ page }) => {
    await page.route('**/api/profile/exists', (r) => json(r, { exists: true }));
    await page.goto('/settings');
    await openInterview(page);
    await page.getByTestId('interview-intro-start').click();
    await page.evaluate(() => {
      const w = window as unknown as { __guardPops: number };
      w.__guardPops = 0;
      window.addEventListener('popstate', () => { w.__guardPops++; });
    });
    await page.getByTestId('answer-textarea').fill(ANSWER);
    await unloadGuardArmed(page);
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('interview-question')).toHaveText(/streaming pipeline/);
    await page.waitForFunction(() => (window as unknown as { __guardPops: number }).__guardPops > 0);
    await page.goBack();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByTestId('edit-unsaved-dialog')).toHaveCount(0);
  });
});

test.describe('F13 — a reload mid-interview resumes at the question', () => {
  test('Start, reload: the current question, not "Bevor es losgeht"', async ({ page }) => {
    await openInterview(page);
    await expect(page.getByTestId('interview-intro')).toContainText('Bevor es losgeht');
    await page.getByTestId('interview-intro-start').click();
    await page.reload();
    await expect(page.getByTestId('interview-question')).toHaveText(/workflow orchestration/);
    await expect(page.getByTestId('interview-intro')).toHaveCount(0);
  });
});

/** The letter page with the regenerate form open; the profile and the generate
 * endpoint are stubbed with WP-FL's write rule. */
async function openLetterForm(page: Page, profileText: string | null) {
  await stubDocuments(page);
  const server = { profileText, bodies: [] as Record<string, unknown>[] };
  await page.route('**/api/profile', (r) =>
    json(r, { profile: server.profileText ? { availability: { text: server.profileText } } : {} }),
  );
  await page.route('**/api/cover-letter/generate', async (r) => {
    const body = JSON.parse(r.request().postData() ?? '{}') as Record<string, unknown>;
    server.bodies.push(body);
    // WP-FL (470f14c3): writes only when the stripped text differs from
    // profile.availability.text, case-insensitive; empty writes nothing.
    const text = typeof body.availability === 'string' ? body.availability.trim() : '';
    if (body.save_availability_to_profile === true && text && text.toLowerCase() !== (server.profileText ?? '').toLowerCase()) {
      server.profileText = text;
    }
    return json(r, { cover_letter_id: CL_ID, status: 'pending' });
  });
  await page.goto(LETTER);
  await page.getByTestId('sidebar-tab-actions').click();
  await page.getByTestId('cl-regenerate-btn').click();
  await expect(page.getByTestId('cover-letter-modal')).toBeVisible();
  return server;
}

test.describe('MD3-1 — the letter form is held', () => {
  test('typed motivation: the shell nav asks; stay keeps it, discard follows the link', async ({ page }) => {
    await openLetterForm(page, null);
    await page.getByTestId('cl-motivation').fill('Ich will in den Zahlungsverkehr wechseln.');
    await page.getByTestId('app-sidebar').getByRole('button', { name: 'Dashboard' }).click();
    const dialog = page.getByTestId('edit-unsaved-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('Angaben noch nicht verwendet');
    await page.getByTestId('edit-unsaved-stay').click();
    await expect(page.getByTestId('cl-motivation')).toHaveValue('Ich will in den Zahlungsverkehr wechseln.');
    await page.getByTestId('app-sidebar').getByRole('button', { name: 'Dashboard' }).click();
    await page.getByTestId('edit-unsaved-discard').click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('typed notice period: back asks, a reload raises the browser prompt', async ({ page }) => {
    await openLetterForm(page, null);
    await page.getByTestId('cl-availability').fill('sofort');
    await unloadGuardArmed(page);
    await page.goBack();
    await expect(page.getByTestId('edit-unsaved-dialog')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${LETTER}$`));
    await page.getByTestId('edit-unsaved-stay').click();
    await expect(page.getByTestId('cl-availability')).toHaveValue('sofort');
    const dialog = page.waitForEvent('dialog');
    await page.close({ runBeforeUnload: true });
    const d = await dialog;
    expect(d.type()).toBe('beforeunload');
    await d.dismiss();
  });

  test('a value pre-filled from the profile is no draft', async ({ page }) => {
    await openLetterForm(page, '3 Monate zum Quartalsende');
    await expect(page.getByTestId('cl-availability')).toHaveValue('3 Monate zum Quartalsende');
    await unloadGuardArmed(page, false);
  });

  test('Abbrechen closes without a question and leaves no guard behind', async ({ page }) => {
    await openLetterForm(page, null);
    await page.getByTestId('cl-salary').fill('95.000 €');
    await unloadGuardArmed(page);
    await page.getByTestId('cl-modal-cancel').click();
    await expect(page.getByTestId('cover-letter-modal')).toHaveCount(0);
    await unloadGuardArmed(page, false);
  });
});

test.describe('B3-11 / MD3-4 — save to profile and the caps', () => {
  test('ticked by default: the notice period is sent with the flag and the server rule saves it', async ({ page }) => {
    const server = await openLetterForm(page, null);
    await expect(page.getByTestId('cl-save-availability')).toBeChecked();
    await expect(page.getByText('Auch im Profil speichern')).toBeVisible();
    await page.getByTestId('cl-availability').fill('3 Monate zum Quartalsende');
    await page.getByTestId('cl-modal-generate').click();
    await expect(page.getByTestId('cover-letter-modal')).toHaveCount(0);
    expect(server.bodies[0]).toMatchObject({ availability: '3 Monate zum Quartalsende', save_availability_to_profile: true });
    expect(server.profileText).toBe('3 Monate zum Quartalsende');
  });

  test('unticked: sent as false, the profile keeps its value', async ({ page }) => {
    const server = await openLetterForm(page, 'sofort');
    await expect(page.getByTestId('cl-availability')).toHaveValue('sofort');
    await page.getByTestId('cl-availability').fill('ab 1. März');
    await page.getByTestId('cl-save-availability').uncheck();
    await page.getByTestId('cl-modal-generate').click();
    await expect(page.getByTestId('cover-letter-modal')).toHaveCount(0);
    expect(server.bodies[0]).toMatchObject({ availability: 'ab 1. März', save_availability_to_profile: false });
    expect(server.profileText).toBe('sofort');
  });

  test('the inputs stop at the caps: motivation 2,000, the rest 300', async ({ page }) => {
    await openLetterForm(page, null);
    await page.getByTestId('cl-motivation').fill('x'.repeat(2100));
    await expect(page.getByTestId('cl-motivation')).toHaveValue('x'.repeat(2000));
    await expect(page.getByTestId('cl-motivation-count')).toHaveText('2.000 / 2.000 Zeichen');
    for (const id of ['cl-recipient-name', 'cl-recipient-company', 'cl-availability', 'cl-salary']) {
      await page.getByTestId(id).fill('y'.repeat(320));
      await expect(page.getByTestId(id)).toHaveValue('y'.repeat(300));
    }
  });
});

test.describe('A-11 — first contact through "Lebenslauf oder Stelle hinzufügen"', () => {
  // The German assertions need the German UI: CI's stub-admin backend serves English settings otherwise.
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/settings', (r) => json(r, { ui_language: 'de', ui_language_explicit: true }));
  });

  test('a profile-less user sees the first-run screen, not the returning-user chooser', async ({ page }) => {
    await page.route('**/api/profile/exists', (r) => json(r, { exists: false }));
    await page.route('**/api/profile/import-jobs**', (r) => json(r, []));
    await page.goto('/dashboard');
    await page.getByTestId('sidebar-nav-import').click();
    await expect(page).toHaveURL(/\/profile\/upload$/);
    await expect(page.getByTestId('profile-update-first-run')).toBeVisible();
    await expect(page.getByTestId('first-run-steps')).toBeVisible();
    await expect(page.getByText('Was hat sich geändert?')).toHaveCount(0);
    await expect(page.getByText('Ich habe eine neue Stelle angetreten')).toHaveCount(0);
  });

  test('a user with a profile keeps the chooser', async ({ page }) => {
    await page.route('**/api/profile/exists', (r) => json(r, { exists: true }));
    await page.goto('/profile/upload');
    await expect(page.getByTestId('profile-update-chooser')).toBeVisible();
    await expect(page.getByText('Ich habe eine neue Stelle angetreten')).toBeVisible();
    await expect(page.getByTestId('profile-update-first-run')).toHaveCount(0);
  });
});
