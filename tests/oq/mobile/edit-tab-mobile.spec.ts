// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/mobile/edit-tab-mobile.spec.ts — #737 (Strawberry build 2, WP-E) at
// 390 px: *Ich bearbeite es selbst* in the review sheet opens the Fine-tune
// sheet with the same context strip the desktop Edit tab shows, and the
// section list there carries no gap badges. Stubbed backend, no provider call.
import { test, expect } from '../../support/auth-fixture';
import { stubDocuments, FLOW_ID } from '../edit-tab-stub';

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

test('#737 phone — the Fine-tune sheet says why the review sent you', async ({ page }) => {
  await stubDocuments(page);
  await page.goto(`/flow/${FLOW_ID}/cv`);
  await expect(page.getByTestId('mobile-command-bar')).toBeVisible({ timeout: 10_000 });
  await page.getByTestId('command-ats').click();
  const sheet = page.getByTestId('command-sheet');
  await sheet.getByTestId('review-action-edit').click();
  await expect(sheet.getByTestId('edit-context-finding')).toContainText('MES-Einführung');
  await expect(sheet.getByTestId('section-textarea')).toHaveValue(/MES-Einführung/);
  await expect(sheet.getByText('Verwandte Lücken')).toHaveCount(0);
});

test('#737 phone — the section list shows the edited tag, not gap counts', async ({ page }) => {
  await stubDocuments(page);
  await page.goto(`/flow/${FLOW_ID}/cv`);
  await page.getByTestId('command-finetune').click();
  const sheet = page.getByTestId('command-sheet');
  await expect(sheet.getByTestId('edit-section-edited-position_w1')).toBeVisible();
  await expect(sheet.getByTestId('edit-sections-intro')).toBeVisible();
});
