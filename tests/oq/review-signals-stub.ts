// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/review-signals-stub.ts — the stubbed letter backend for the #702 / #703
// review-signal specs (synthetic operations_marcus_de, 2026-09-13 replay). Not a spec.
import type { Page } from '@playwright/test';
import { FLOW_ID, CL_ID, CV_ID, JOB_ID, TRUTH, SETTINGS, json } from './review-letter-stub';
import { readFileSync } from 'node:fs';

const data = JSON.parse(readFileSync(new URL('./review-signals-data.json', import.meta.url), 'utf-8'));

/** `clean` empties group 1 (no unsupported term), so the verdict is the all-clear the R-1 condition rewords. */
export async function stub(page: Page, uiLanguage: 'de' | 'en' = 'de', opts: { clean?: boolean } = {}) {
  const ats = opts.clean
    ? { ...data.ats, keywords: { ...data.ats.keywords, present_unsupported: [], present_unsupported_matches: {} } }
    : data.ats;
  const server = { kept: [] as string[], takenOut: [] as string[] };
  let state: Record<string, unknown> = {};
  await page.route('**/api/settings', (r) => json(r, { ...SETTINGS, ui_language: uiLanguage }));
  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) =>
    json(r, {
      flow_id: FLOW_ID, current_step: 'complete', available_actions: {}, job_id: JOB_ID, application_id: null,
      job_summary: { role_title: 'Leiter Operations' }, gap_summary: { match_score: 0.744 },
      cv_summary: { cv_id: CV_ID, pdf_url: '', expires_at: '2026-12-22T00:00:00Z' },
      cover_letter_summary: { cover_letter_id: CL_ID, status: 'ready', template: 'classic_german' },
    }),
  );
  await page.route('**/api/flow/*/advance', (r) => json(r, {}));
  await page.route(`**/api/cover-letter/${CL_ID}/status`, (r) =>
    json(r, {
      cover_letter_id: CL_ID, status: 'ready', letter_data: data.letter_data, section_overrides: {},
      document_language: 'de', signature_override: null, signature_effective: false, signature_available: false,
      critic_report: data.critic,
    }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/html`, (r) =>
    r.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `<html><body>${(data.letter_data.body.paragraphs as string[]).map((p) => `<p>${p}</p>`).join('')}</body></html>`,
    }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/ats-report`, (r) =>
    json(r, { document_id: CL_ID, status: 'ready', report: ats, review_state: state }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/truthfulness-report`, (r) => json(r, { report: TRUTH }));
  await page.route(`**/api/cover-letter/${CL_ID}/critic-report`, (r) => json(r, { report: data.critic }));
  await page.route(`**/api/job/${JOB_ID}/gaps`, (r) => json(r, { unasked_requirements: [] }));
  await page.route(`**/api/cover-letter/${CL_ID}/review/kept`, async (r) => {
    const body = JSON.parse(r.request().postData() ?? '{}');
    server.kept.push(body.finding_key);
    state = {
      walked_at: null,
      decisions: body.keep ? [{ finding_key: body.finding_key, label: 'x', action: 'kept', at: '2026-10-07T12:00:00Z', undo: null }] : [],
    };
    return json(r, { review_state: state });
  });
  return server;
}

/** The CV page behind *In den Lebenslauf übernehmen* — enough to render its tabs. */
export async function stubCv(page: Page) {
  await page.route(`**/api/cv/${CV_ID}/html`, (r) =>
    r.fulfill({ status: 200, contentType: 'text/html', body: '<html><body><p>Marcus</p></body></html>' }),
  );
  await page.route(`**/api/cv/${CV_ID}/status`, (r) =>
    json(r, { document_language: 'de', template: 'classic_german', signature_available: false }),
  );
  await page.route(`**/api/cv/${CV_ID}/ats-report`, (r) => json(r, { report: null, review_state: {} }));
  await page.route(`**/api/cv/${CV_ID}/truthfulness-report`, (r) => json(r, { report: TRUTH }));
  await page.route(`**/api/cv/${CV_ID}/critic-report`, (r) =>
    json(r, { report: { ran: true, mount: 'cv', advisories: [], dropped_citations: 0 } }),
  );
  await page.route(`**/api/cv/${CV_ID}/sections`, (r) => json(r, { sections: [], general_gaps: [] }));
}
