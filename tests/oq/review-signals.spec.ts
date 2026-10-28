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

// ADR-099 cl. 8 superseded #702's cross-document section and #703's group-2
// tag on the surface (both are class S now; agents still read them in the
// report). On the same replayed data the letter review shows neither, and the
// headline never borrows the cross-read's wording.
test.describe('#702 / #703 under ADR-099 cl. 8 — the signals stay in the report, off the surface', () => {
  test('no cross-document section, no group-2 tag, no "nachgefordert" on the letter review', async ({ page }) => {
    await stub(page);
    await page.goto(`/flow/${FLOW_ID}/cover-letter`);
    await expect(page.getByTestId('review-verdict')).toBeVisible();
    await expect(page.getByTestId('review-xdoc')).toHaveCount(0);
    await expect(page.getByTestId('review-group-2')).toHaveCount(0);
    await expect(page.getByTestId('review-item-signal-tag')).toHaveCount(0);
    await expect(page.getByTestId('review-surface')).not.toContainText('nachgefordert');
    await expect(page.getByTestId('review-surface')).not.toContainText('Sauberraumbereich seit 2021');
  });

  test('the headline never carries the cross-read sentence', async ({ page }) => {
    await stub(page, 'de', { clean: true });
    await page.goto(`/flow/${FLOW_ID}/cover-letter`);
    const verdict = page.getByTestId('review-verdict');
    await expect(verdict).toBeVisible();
    await expect(verdict).not.toContainText('Neben Deinem Lebenslauf');
  });
});
