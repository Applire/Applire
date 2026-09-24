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

// tests/oq/review-letter-stub.ts — the stubbed cover-letter backend shared by the
// ADR-090 delivery-fix specs (desktop and 390 px). Not a spec itself.
import type { Page, Route } from '@playwright/test';

export const FLOW_ID = 'f0a0f0a0-f0a0-f0a0-f0a0-f0a0f0a0f0a0';
export const CL_ID = 'c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1';
export const CV_ID = 'c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2';
export const JOB_ID = 'b0b0b0b0-b0b0-b0b0-b0b0-b0b0b0b0b0b0';

const RAW_BODY = [
  'I built European e-commerce platforms for ten years. I also ran the payment stack.',
  'I led settlement for merchants across five markets.',
];
const TAKEN_BODY = 'I built platforms for ten years. I also ran the payment stack.\n\nI led settlement for merchants across five markets.';

export const json = (r: Route, body: unknown, status = 200) =>
  r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

export function atsReport(terms: string[], matches: Record<string, Array<{ form: string; stem: boolean }>>) {
  return {
    checks: [{ id: 'contact-0', status: 'pass' }],
    keywords: {
      present: ['SQL'],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: terms,
      present_unsupported_matches: matches,
      claimable_concepts: [],
    },
  };
}
export const TRUTH = { version: '1', document_kind: 'cover_letter', claims: [], counts: {}, stated_limit: '' };
export const SETTINGS = {
  default_color_profile_id: null,
  default_accent_hex: null,
  ui_language: 'en',
  ui_language_explicit: true,
  hide_predownload_notice: true,
  dismissed_explainers: [],
  target_cv_pages: 2,
};

/** The letter page, with a server that remembers the body override. */
export async function stubLetter(page: Page) {
  const server = { override: null as string | null, patches: [] as string[] };
  const letterHtml = () =>
    `<html><body>${(server.override ? server.override.split('\n\n') : RAW_BODY)
      .map((p) => `<p>${p}</p>`)
      .join('')}</body></html>`;
  const report = () =>
    server.override
      ? atsReport(['Settlement'], { Settlement: [{ form: 'settlement', stem: false }] })
      : atsReport(['European e-commerce', 'Settlement'], {
          'European e-commerce': [{ form: 'European e-commerce', stem: false }],
          Settlement: [{ form: 'settlement', stem: false }],
        });

  await page.route('**/api/settings', (r) => json(r, SETTINGS));
  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) =>
    json(r, {
      flow_id: FLOW_ID,
      current_step: 'complete',
      available_actions: {},
      job_id: JOB_ID,
      application_id: null,
      job_summary: { role_title: 'Payments Lead' },
      gap_summary: { match_score: 0.6 },
      cv_summary: { cv_id: CV_ID, pdf_url: '', expires_at: '2026-12-22T00:00:00Z' },
      cover_letter_summary: { cover_letter_id: CL_ID, status: 'ready', template: 'classic_german' },
    }),
  );
  await page.route('**/api/flow/*/advance', (r) => json(r, {}));
  await page.route(`**/api/cover-letter/${CL_ID}/status`, (r) =>
    json(r, {
      cover_letter_id: CL_ID,
      status: 'ready',
      letter_data: { body: { paragraphs: RAW_BODY }, header: { name: 'Dana Test' } },
      // NOTE C-4: the status response carries the saved overrides.
      section_overrides: server.override ? { body: server.override } : {},
      document_language: 'en',
      signature_override: null,
      signature_effective: false,
      signature_available: false,
    }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/html`, (r) =>
    r.fulfill({ status: 200, contentType: 'text/html', body: letterHtml() }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/ats-report`, (r) =>
    json(r, { document_id: CL_ID, status: 'ready', report: report(), review_state: {} }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/truthfulness-report`, (r) => json(r, { report: TRUTH }));
  await page.route(`**/api/cover-letter/${CL_ID}/critic-report`, (r) =>
    json(r, { report: { ran: true, mount: 'letter', advisories: [], dropped_citations: 0 } }),
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (r) => json(r, { unasked_requirements: [] }));
  await page.route(`**/api/cover-letter/${CL_ID}/review/take-out`, (r) => {
    server.override = TAKEN_BODY;
    return json(r, {
      changes: [{ section_id: 'body', before: RAW_BODY.join('\n\n'), after: TAKEN_BODY }],
      still_listed: false,
      report: { document_id: CL_ID, status: 'ready', report: report() },
      truthfulness: TRUTH,
      review_state: {
        walked_at: null,
        decisions: [
          {
            finding_key: 'ats:european e commerce',
            label: 'European e-commerce',
            action: 'taken_out',
            at: '2026-09-24T10:00:00Z',
            undo: { sections: [{ section_id: 'body', before: RAW_BODY.join('\n\n') }] },
          },
        ],
      },
    });
  });
  await page.route(`**/api/cover-letter/${CL_ID}/section`, async (r) => {
    const body = JSON.parse(r.request().postData() ?? '{}');
    server.patches.push(body.content);
    server.override = body.content;
    return json(r, {});
  });
  await page.route(`**/api/cover-letter/${CL_ID}/review/edited`, (r) =>
    json(r, { report: { document_id: CL_ID, status: 'ready', report: report() }, truthfulness: TRUTH, review_state: {} }),
  );
  return server;
}

