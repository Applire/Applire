// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/review-signals.spec.ts — #702 / #703 on the letter's review panel,
// every backend call stubbed (`page.route`, no provider call). The data is the
// synthetic operations_marcus_de delivery run of 2026-09-13, replayed through the
// new backend code (0 provider calls): the critic report with its derived
// `cross_document`, and the ATS report whose `terminal-review` check carries the
// repeated-demand signals.
import { test, expect } from '../support/auth-fixture';
import { FLOW_ID } from './review-letter-stub';
import { stub } from './review-signals-stub';

test.describe('#702 / #703 — review signals on the letter', () => {
  test('one weighted cross-document card instead of three benign rows, with the three actions', async ({ page }) => {
    const server = await stub(page);
    await page.goto(`/flow/${FLOW_ID}/cover-letter`);
    const section = page.getByTestId('review-xdoc');
    await expect(section).toBeVisible();
    const card = section.getByTestId('review-xdoc-card');
    await expect(card).toHaveAttribute('data-weight', 'high');
    await expect(card).toContainText('Im Anschreiben, nicht im Lebenslauf');
    await expect(card.getByTestId('review-xdoc-concepts')).toContainText('Sauberraumbereich seit 2021');
    await expect(card.getByTestId('review-xdoc-take-out')).toBeVisible();
    await expect(card.getByTestId('review-xdoc-keep')).toBeVisible();
    // the three advisories of the transfer sentence are no longer group-4 rows
    await expect(page.getByTestId('review-group-count-4')).not.toHaveText('7');

    await card.getByTestId('review-xdoc-keep').click();
    await expect.poll(() => server.kept.length).toBe(1);
  });

  test('the open repeated demand is tagged on its group-2 row', async ({ page }) => {
    await stub(page);
    await page.goto(`/flow/${FLOW_ID}/cover-letter`);
    const toggle = page.getByTestId('review-group-toggle-2');
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    const tag = page.getByTestId('review-group-2').getByTestId('review-item-signal-tag');
    await expect(tag).toHaveText('2× nachgefordert');
  });
});
