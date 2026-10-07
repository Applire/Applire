// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/edit-tab.spec.ts — #737 (Strawberry build 2, WP-E): the Edit tab
// next to the ADR-090 review panel, driven in a real browser against stubs.
//
// What needs a browser here: the tab strip unmounting the editor (the draft
// guard), the portalled dialogs, the review card → Edit tab hand-over, and the
// letter's cross-document card landing on the CV page with its facts.
// Every backend call is a `page.route` (tests/oq/edit-tab-stub.ts) — no
// provider call.
import { test, expect } from '../support/auth-fixture';
import { stubDocuments, FLOW_ID } from './edit-tab-stub';
import type { Page } from '@playwright/test';

// The desktop panel. On the CV page a phone sheet with a second editor is
// mounted too (hidden at this width), so every editor locator is scoped here.
const panel = (page: Page) => page.getByTestId('sidebar-body');

test.use({
  authUser: {
    id: '00000000-0000-0000-0000-0000000000a2',
    email: 'marcus@example.org',
    role: 'user',
    has_password: true,
    oidc_linked: false,
    ui_language: 'de',
  },
});

const CV = `/flow/${FLOW_ID}/cv`;
const LETTER = `/flow/${FLOW_ID}/cover-letter`;

test.describe('#737 — CV Edit tab', () => {
  test('lists sections without gap counts and marks the edited one', async ({ page }) => {
    await stubDocuments(page);
    await page.goto(CV);
    await page.getByTestId('sidebar-tab-edit').click();
    await expect(panel(page).getByTestId('edit-sections-intro')).toBeVisible();
    await expect(panel(page).getByTestId('edit-section-edited-position_w1')).toHaveText('bearbeitet');
    await expect(panel(page).getByTestId('edit-section-edited-skills')).toHaveCount(0);
    // The section list carries no gap badge; the gap lives on Prüfung (group 2/3).
    await panel(page).getByRole('button', { name: /Weberit/ }).click();
    await expect(panel(page).getByTestId('section-textarea')).toBeVisible();
    await expect(panel(page).getByText('Verwandte Lücken')).toHaveCount(0);
    await expect(panel(page).getByTestId('write-myself-btn')).toHaveCount(0);
  });

  test('"Ich bearbeite es selbst" lands with the reason, and the save says what the review found', async ({ page }) => {
    const server = await stubDocuments(page);
    await page.goto(CV);
    await page.getByTestId('review-action-edit').click();
    await expect(panel(page).getByTestId('edit-context-finding')).toContainText('MES-Einführung');
    await expect(panel(page).getByTestId('edit-context-label')).toHaveText('Industrie 4.0');
    const textarea = panel(page).getByTestId('section-textarea');
    await expect(textarea).toHaveValue(/MES-Einführung/);
    await textarea.fill('- Führte 38 Mitarbeitende\n- Leitete die Einführung eines Maschinendatensystems');
    await panel(page).getByTestId('section-save').click();
    // First save asks for the scope (unchanged SaveScopePrompt).
    await page.getByRole('button', { name: 'Nur für diesen Lebenslauf' }).click();
    await expect(panel(page).getByTestId('edit-receipt-cleared')).toContainText('„Industrie 4.0“ ist in der Prüfung nicht mehr markiert.');
    await expect(panel(page).getByTestId('edit-receipt-cleared')).toContainText('Keine Stelle mehr offen.');
    expect(server.edited).toHaveLength(1);
    await panel(page).getByTestId('edit-receipt-back').click();
    await expect(page.getByTestId('sidebar-tab-review')).toHaveAttribute('aria-selected', 'true');
  });

  test('a still-flagged place is said as still flagged', async ({ page }) => {
    await stubDocuments(page);
    await page.goto(CV);
    await page.getByTestId('review-action-edit').click();
    const textarea = panel(page).getByTestId('section-textarea');
    await textarea.fill('- Leitete die MES-Einführung an 14 Spritzgussmaschinen (neu formuliert)');
    await panel(page).getByTestId('section-save').click();
    await page.getByRole('button', { name: 'Nur für diesen Lebenslauf' }).click();
    await expect(panel(page).getByTestId('edit-receipt-still')).toContainText('weiterhin markiert');
    await expect(panel(page).getByTestId('edit-receipt-cleared')).toHaveCount(0);
  });

  test('a re-audit without a report never reads as "nothing open" (ADR-081 cl. 9)', async ({ page }) => {
    await stubDocuments(page, 'de', { editedReportMissing: true });
    await page.goto(CV);
    await page.getByTestId('review-action-edit').click();
    await panel(page).getByTestId('section-textarea').fill('- Leitete die Einführung eines Maschinendatensystems');
    await panel(page).getByTestId('section-save').click();
    await page.getByRole('button', { name: 'Nur für diesen Lebenslauf' }).click();
    await expect(panel(page).getByTestId('edit-receipt-plain')).toBeVisible();
    await expect(panel(page).getByTestId('edit-receipt-cleared')).toHaveCount(0);
  });

  test('leaving Edit with a draft asks; stay keeps it, discard drops it', async ({ page }) => {
    await stubDocuments(page);
    await page.goto(CV);
    await page.getByTestId('sidebar-tab-edit').click();
    await panel(page).getByRole('button', { name: /Weberit/ }).click();
    await panel(page).getByTestId('section-textarea').fill('- Entwurf');
    await page.getByTestId('sidebar-tab-review').click();
    const dialog = page.getByTestId('edit-unsaved-dialog');
    await expect(dialog).toContainText('Produktionsleiter — Weberit Kunststofftechnik GmbH');
    await page.getByTestId('edit-unsaved-stay').click();
    await expect(panel(page).getByTestId('section-textarea')).toHaveValue('- Entwurf');
    await page.getByTestId('sidebar-tab-review').click();
    await page.getByTestId('edit-unsaved-discard').click();
    await expect(page.getByTestId('sidebar-tab-review')).toHaveAttribute('aria-selected', 'true');
    await page.getByTestId('sidebar-tab-edit').click();
    await expect(panel(page).getByTestId('section-textarea')).toHaveCount(0);
  });

  test('save-and-switch writes the draft to THIS CV and switches', async ({ page }) => {
    const server = await stubDocuments(page);
    await page.goto(CV);
    await page.getByTestId('sidebar-tab-edit').click();
    await panel(page).getByRole('button', { name: /Weberit/ }).click();
    await panel(page).getByTestId('section-textarea').fill('- Entwurf gespeichert');
    await page.getByTestId('sidebar-tab-actions').click();
    await page.getByTestId('edit-unsaved-save').click();
    await expect(page.getByTestId('cv-actions-regenerate')).toBeVisible();
    expect(server.patches).toHaveLength(1);
    expect(server.patches[0].body).toEqual({ content: '- Entwurf gespeichert', save_to_profile: false });
  });

  test('every new-version path asks first and names the edited section', async ({ page }) => {
    const server = await stubDocuments(page);
    await page.goto(CV);
    await page.getByTestId('sidebar-tab-actions').click();
    await page.getByTestId('cv-actions-regenerate').click();
    const dialog = page.getByTestId('edit-new-version-dialog');
    await expect(page.getByTestId('edit-new-version-loss')).toContainText('Produktionsleiter — Weberit Kunststofftechnik GmbH');
    await page.getByTestId('edit-new-version-cancel').click();
    await expect(dialog).toHaveCount(0);
    expect(server.generates).toHaveLength(0);
    await page.getByTestId('cv-actions-regenerate').click();
    await page.getByTestId('edit-new-version-confirm').click();
    await expect.poll(() => server.generates.length).toBe(1);
  });
});

test.describe('#737 — cover letter', () => {
  test('Edit tab states the inherited look instead of seven template buttons', async ({ page }) => {
    await stubDocuments(page);
    await page.goto(LETTER);
    await page.getByTestId('sidebar-tab-edit').click();
    await expect(page.getByTestId('letter-look')).toContainText('übernimmt das Anschreiben von Deinem Lebenslauf: Klassisch.');
    await expect(page.locator('[data-testid^="cl-template-"]')).toHaveCount(0);
  });

  test('regenerating a letter with an edited body asks first', async ({ page }) => {
    await stubDocuments(page, 'de', { letterBodyEdited: true });
    await page.goto(LETTER);
    await page.getByTestId('sidebar-tab-actions').click();
    await page.getByTestId('cl-regenerate-btn').click();
    await expect(page.getByTestId('edit-new-version-loss')).toContainText('Anschreiben-Text');
    await page.getByTestId('edit-new-version-confirm').click();
    await expect(page.getByTestId('cover-letter-modal')).toBeVisible();
  });

  test('"In den Lebenslauf übernehmen" lands on the CV Edit tab with the letter-only facts', async ({ page }) => {
    await stubDocuments(page);
    await page.goto(LETTER);
    await page.getByTestId('review-xdoc-add-to-cv').first().click();
    await expect(page).toHaveURL(/\/cv\?tab=edit&xdoc=/);
    const strip = panel(page).getByTestId('edit-context-letter');
    await expect(strip).toBeVisible();
    await expect(panel(page).getByTestId('edit-context-facts')).toContainText('Kosmetik-Verpackungen');
    await expect(panel(page).getByTestId('edit-context-facts')).toContainText('ISO-9001-Audit-Praxis');
    await panel(page).getByTestId('edit-context-back').click();
    await expect(page).toHaveURL(new RegExp(`${LETTER}$`));
  });
});
