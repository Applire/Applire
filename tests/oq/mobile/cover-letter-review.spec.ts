// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

// tests/oq/mobile/cover-letter-review.spec.ts — D-4 (ADR-090 delivery run):
// at 390 px the letter's review was unreachable (no command bar). The letter
// now mounts the same MobileCommandBar as the CV, with the live surface.
import { test, expect } from '@playwright/test';
import { FLOW_ID, stubLetter } from '../review-letter-stub';

test.describe('D-4 — the letter review at 390x844', () => {
  test('the command bar opens the live review, and Show me where opens the phone locate view', async ({ page }) => {
    await stubLetter(page);
    await page.goto(`/flow/${FLOW_ID}/cover-letter`);
    await expect(page.getByTestId('mobile-command-bar')).toBeVisible();
    await expect(page.getByTestId('command-ats-open-badge')).toHaveText('2');
    await page.getByTestId('command-ats').click();
    const sheet = page.getByTestId('command-sheet');
    const card = sheet.getByTestId('review-card');
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute('data-finding-key', 'ats:european e commerce');

    await card.getByTestId('review-action-locate').click();
    await expect(page.getByTestId('review-locate-back')).toBeVisible();
    await expect(page.getByTestId('review-locate-place')).toHaveText('“European e-commerce” · place 1 of 1');
    await expect(sheet).toBeHidden();
    const marks = page.frameLocator('[data-testid="cover-letter-iframe"]').locator('mark[data-applire-hit]');
    await expect(marks).toHaveCount(1);

    await page.getByTestId('review-locate-back').click();
    await expect(sheet.getByTestId('review-card')).toBeVisible();
  });
});
