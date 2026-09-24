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

// tests/oq/review-recut-delivery-fixes.spec.ts
//
// ADR-090 real-path delivery run, defects D-1 … D-3, pinned in a browser with
// every backend call stubbed (`page.route`, no provider call):
//  * D-2 (HIGH) — take out on the letter, then *Let me edit it* + save: the
//    PATCH must carry the taken-out body, never the pre-take-out wording.
//  * D-3 — the result names only the changed sentence, the rest elided.
//  * D-1 — a CV position change is labelled with the section's label, not
//    `position::<uuid>`.
import { test, expect } from '@playwright/test';
import { FLOW_ID, CV_ID, JOB_ID, TRUTH, SETTINGS, json, atsReport, stubLetter } from './review-letter-stub';

test.describe('ADR-090 delivery fixes — the letter (D-2, D-3)', () => {
  test('take out → Let me edit it → save writes the taken-out body, never the removed wording', async ({ page }) => {
    const server = await stubLetter(page);
    await page.goto(`/flow/${FLOW_ID}/cover-letter`);
    const card = page.getByTestId('review-card');
    await expect(card).toHaveAttribute('data-finding-key', 'ats:european e commerce');

    await card.getByTestId('review-action-takeout').click();
    await expect(page.getByTestId('review-takeout-done')).toBeVisible();

    // D-3: only the changed sentence, struck, then its replacement; the rest elided.
    await expect(page.getByTestId('review-takeout-before')).toHaveText([
      'I built European e-commerce platforms for ten years.',
    ]);
    await expect(page.getByTestId('review-takeout-after')).toHaveText(['I built platforms for ten years.']);
    await expect(page.getByTestId('review-takeout-change')).not.toContainText('five markets');

    // D-2: open the remaining finding and edit it yourself.
    await page.getByTestId('review-item-g1-ats:settlement').click();
    await page.getByTestId('review-action-edit').click();
    // The phone's Fine-tune sheet mounts its own (md:hidden) editor too — act on the visible one.
    const textarea = page.locator('[data-testid="cl-body-textarea"]:visible');
    await expect(textarea).toBeVisible();
    await expect(textarea).not.toHaveValue(/European e-commerce/);
    await textarea.fill((await textarea.inputValue()).replace('settlement', 'payouts'));
    await page.locator('[data-testid="cl-save-body-btn"]:visible').click();

    await expect.poll(() => server.patches.length).toBe(1);
    expect(server.patches[0]).not.toContain('European e-commerce');
    expect(server.patches[0]).toContain('I built platforms for ten years.');
    expect(server.patches[0]).toContain('payouts');
  });
});

test.describe('ADR-090 delivery fixes — the CV (D-1)', () => {
  test('a position change is labelled with the section label, not position::<uuid>', async ({ page }) => {
    const POS = 'position::00647bf1-004f-4a4a-8a8a-000000000001';
    const BEFORE = 'Built the shipment-tracking backend.\nServed ~40,000 customers.\nOwned the AWS deployment.';
    const AFTER = 'Built the shipment-tracking backend.\nOwned the AWS deployment.';
    await page.route('**/api/settings', (r) => json(r, SETTINGS));
    await page.route(`**/api/flow/${FLOW_ID}/state`, (r) =>
      json(r, {
        flow_id: FLOW_ID,
        current_step: 'complete',
        available_actions: {},
        job_id: JOB_ID,
        application_id: null,
        job_summary: { role_title: 'Backend Engineer' },
        gap_summary: { match_score: 0.6, gaps: [], sections: [] },
        cv_summary: { cv_id: CV_ID, pdf_url: '', expires_at: '2026-12-22T00:00:00Z' },
      }),
    );
    await page.route('**/api/flow/*/advance', (r) => json(r, {}));
    await page.route(`**/api/cv/${CV_ID}/html`, (r) =>
      r.fulfill({ status: 200, contentType: 'text/html', body: `<html><body><p>${BEFORE}</p></body></html>` }),
    );
    await page.route(`**/api/cv/${CV_ID}/status`, (r) =>
      json(r, { document_language: 'en', template: 'classic_german', signature_available: false }),
    );
    const report = atsReport(['Customer scale'], { 'Customer scale': [{ form: '40,000 customers', stem: false }] });
    await page.route(`**/api/cv/${CV_ID}/ats-report`, (r) => json(r, { report, review_state: {} }));
    await page.route(`**/api/cv/${CV_ID}/truthfulness-report`, (r) => json(r, { report: TRUTH }));
    await page.route(`**/api/cv/${CV_ID}/critic-report`, (r) =>
      json(r, { report: { ran: true, mount: 'cv', advisories: [], dropped_citations: 0 } }),
    );
    await page.route(`**/api/cv/${CV_ID}/sections`, (r) =>
      json(r, {
        sections: [
          {
            section_id: POS,
            label: 'Senior Backend Engineer — Cargonaut Logistics GmbH',
            content: BEFORE,
            has_override: false,
            gaps: [],
          },
        ],
        general_gaps: [],
      }),
    );
    await page.route(`**/api/cv/${CV_ID}/review/take-out`, (r) =>
      json(r, {
        changes: [{ section_id: POS, before: BEFORE, after: AFTER }],
        still_listed: false,
        report: { document_id: CV_ID, status: 'ready', report: atsReport([], {}) },
        truthfulness: TRUTH,
        review_state: {
          walked_at: null,
          decisions: [
            {
              finding_key: 'ats:customer scale',
              label: 'Customer scale',
              action: 'taken_out',
              at: '2026-09-24T10:00:00Z',
              undo: { sections: [{ section_id: POS, before: BEFORE }] },
            },
          ],
        },
      }),
    );
    await page.goto(`/flow/${FLOW_ID}/cv`);
    await page.getByTestId('review-action-takeout').first().click();
    const change = page.getByTestId('review-takeout-change');
    await expect(change).toContainText('Senior Backend Engineer — Cargonaut Logistics GmbH');
    await expect(change).not.toContainText('position::');
    await expect(page.getByTestId('review-takeout-before')).toHaveText(['Served ~40,000 customers.']);
    await expect(change).not.toContainText('shipment-tracking');
  });
});
