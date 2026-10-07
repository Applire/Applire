// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// tests/oq/edit-tab-stub.ts — the stubbed CV + cover-letter backend for the #737
// Edit-tab specs (Strawberry build 2, WP-E). Synthetic case only (an operations
// lead, "Marcus"), shaped after `tests/files/panel_review_case/`. Not a spec.
//
// Every backend call is a `page.route`, so the specs that use it spend no
// provider call. `server` records what the page sent, so a spec can assert the
// request instead of only the rendered state.
import type { Page, Route } from '@playwright/test';
import { readFileSync } from 'node:fs';

/** R's synthetic Marcus critic report (#702) — carries a high-weight `cross_document` item. */
export const LETTER_CRITIC = JSON.parse(
  readFileSync(new URL('./review-signals-data.json', import.meta.url), 'utf-8'),
).critic;
export const XDOC_KEY: string = LETTER_CRITIC.cross_document[0].key;

export const FLOW_ID = 'e737e737-e737-e737-e737-e737e737e737';
export const CV_ID = 'c737c737-c737-c737-c737-c737c737c737';
export const CL_ID = 'd737d737-d737-d737-d737-d737d737d737';
export const JOB_ID = 'b737b737-b737-b737-b737-b737b737b737';
export const APP_ID = 'a737a737-a737-a737-a737-a737a737a737';

export const json = (r: Route, body: unknown, status = 200) =>
  r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

export const SECTIONS = [
  {
    section_id: 'introduction',
    label: 'Introduction',
    content:
      'Produktionsleiter mit 14 Jahren Erfahrung in der diskreten Fertigung. Führt Teams bis 90 Mitarbeitende im Dreischichtbetrieb.',
    has_override: false,
    gaps: [],
  },
  {
    section_id: 'position_w1',
    label: 'Produktionsleiter — Weberit Kunststofftechnik GmbH',
    content:
      '- Führte 38 Mitarbeitende über drei Schichtleiter im Dreischichtbetrieb\n- Steigerte die Termintreue von 87 % auf 96 %\n- Leitete die MES-Einführung an 14 Spritzgussmaschinen',
    has_override: true,
    gaps: [{ id: 'gap-sap-pp', label: 'SAP PP', kind: 'claimable' }],
  },
  {
    section_id: 'position_w2',
    label: 'Schichtleiter — Rasselstein Umformtechnik GmbH',
    content: '- Führte eine Schicht mit 14 Mitarbeitenden\n- Einführung von 5S und SMED an zwei Linien',
    has_override: false,
    gaps: [],
  },
  {
    section_id: 'skills',
    label: 'Skills',
    content: 'Lean Management, Shopfloor-Management, KVP, 5S, Kaizen, SMED, SAP MM',
    has_override: false,
    gaps: [{ id: 'gap-ifs', label: 'IFS Food', kind: 'honest' }],
  },
];

export const GENERAL_GAPS = [{ id: 'gap-brc', label: 'BRC-Zertifizierung', kind: 'honest' }];

export const ATS = {
  checks: [
    { id: 'contact-0', status: 'pass' },
    { id: 'headings-0', status: 'pass' },
    { id: 'page-length-0', status: 'pass' },
  ],
  keywords: {
    present: ['SAP MM', 'Lean Management'],
    missing: ['SAP PP', 'IFS Food', 'BRC-Zertifizierung'],
    missing_claimable: ['SAP PP'],
    missing_honest_gap: ['IFS Food'],
    present_unsupported: ['Industrie 4.0'],
    present_unsupported_matches: { 'Industrie 4.0': [{ form: 'MES-Einführung', stem: false }] },
    claimable_concepts: [],
  },
};

export const TRUTH = { version: '1', document_kind: 'cv', claims: [], counts: {}, stated_limit: '' };

export const SETTINGS = {
  default_color_profile_id: null,
  default_accent_hex: null,
  ui_language: 'de',
  ui_language_explicit: true,
  hide_predownload_notice: true,
  dismissed_explainers: ['fact_pins_intro'],
  target_cv_pages: 2,
  signature_in_cv: false,
};

const LETTER_DATA = {
  header: { name: 'Marcus Beispiel', email: 'marcus@example.org' },
  recipient: { name: 'Frau Kranz', company: 'Rheinwerk Verpackungen GmbH' },
  body: {
    paragraphs: [
      'mit 14 Jahren Erfahrung in der diskreten Fertigung bewerbe ich mich als Leiter Operations.',
      'Bei Weberit Kunststofftechnik GmbH führte ich 38 Mitarbeitende über drei Schichtleiter im Dreischichtbetrieb.',
      'Über die Gelegenheit zu einem persönlichen Gespräch freue ich mich.',
    ],
  },
  signature: { closing: 'Mit freundlichen Grüßen', name: 'Marcus Beispiel' },
};

const CV_HTML = `<html><body style="font-family:serif;padding:32px">
<h1>Marcus Beispiel</h1><p>${SECTIONS[0].content}</p>
<h2>Berufserfahrung</h2><h3>Produktionsleiter — Weberit Kunststofftechnik GmbH</h3>
<ul><li>Führte 38 Mitarbeitende über drei Schichtleiter im Dreischichtbetrieb</li>
<li>Steigerte die Termintreue von 87 % auf 96 %</li><li>Leitete die MES-Einführung an 14 Spritzgussmaschinen</li></ul>
<h3>Schichtleiter — Rasselstein Umformtechnik GmbH</h3><ul><li>Führte eine Schicht mit 14 Mitarbeitenden</li>
<li>Einführung von 5S und SMED an zwei Linien</li></ul>
<h2>Kenntnisse</h2><p>${SECTIONS[3].content}</p></body></html>`;

export interface EditServer {
  patches: Array<{ url: string; body: unknown }>;
  rewrites: Array<{ url: string; body: unknown }>;
  generates: unknown[];
  edited: unknown[];
}

/** Stubs every call the CV page and the letter page make. */
export async function stubDocuments(
  page: Page,
  uiLanguage: 'de' | 'en' = 'de',
  opts: { letterBodyEdited?: boolean; editedReportMissing?: boolean } = {},
): Promise<EditServer> {
  const server: EditServer = { patches: [], rewrites: [], generates: [], edited: [] };
  let sections = SECTIONS.map((s) => ({ ...s }));
  await page.route('**/api/settings', (r) => json(r, { ...SETTINGS, ui_language: uiLanguage }));
  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) =>
    json(r, {
      flow_id: FLOW_ID,
      current_step: 'complete',
      available_actions: {},
      job_id: JOB_ID,
      application_id: APP_ID,
      job_summary: { role_title: 'Leiter Operations' },
      gap_summary: { match_score: 0.74, gaps: [], sections: [], detected_company: null, current_accent_hex: '#12233E' },
      cv_summary: { cv_id: CV_ID, pdf_url: '', expires_at: '2026-12-22T00:00:00Z' },
      cover_letter_summary: { cover_letter_id: CL_ID, status: 'ready', template: 'classic_german' },
    }),
  );
  await page.route('**/api/flow/*/advance', (r) => json(r, {}));
  await page.route(`**/api/applications/${APP_ID}`, (r) =>
    json(r, { id: APP_ID, user_status: 'tracking', applied_at: null, submitted_cv_id: null }),
  );
  await page.route(`**/api/applications/${APP_ID}/pins`, (r) => json(r, { pins: [] }));
  await page.route(`**/api/applications/${APP_ID}/pins**`, (r) => json(r, { pins: [] }));
  await page.route('**/api/color-profiles**', (r) => json(r, []));
  // CV
  await page.route(`**/api/cv/${CV_ID}/html`, (r) => r.fulfill({ status: 200, contentType: 'text/html', body: CV_HTML }));
  await page.route(`**/api/cv/${CV_ID}/status`, (r) =>
    json(r, { document_language: 'de', template: 'classic_german', signature_available: false, signature_effective: false }),
  );
  await page.route(`**/api/cv/${CV_ID}/ats-report`, (r) => json(r, { report: ATS, review_state: {} }));
  await page.route(`**/api/cv/${CV_ID}/truthfulness-report`, (r) => json(r, { report: TRUTH }));
  await page.route(`**/api/cv/${CV_ID}/critic-report`, (r) =>
    json(r, { report: { ran: true, mount: 'cv', advisories: [], dropped_citations: 0 } }),
  );
  await page.route(`**/api/cv/${CV_ID}/sections`, (r) => json(r, { sections, general_gaps: GENERAL_GAPS }));
  // ADR-090 cl. 5: the save from a finding reports `edited`; the re-audit no
  // longer lists "Industrie 4.0" once the MES wording is gone.
  await page.route(`**/api/cv/${CV_ID}/review/edited`, async (r) => {
    server.edited.push(JSON.parse(r.request().postData() ?? '{}'));
    const stillThere = sections.some((s) => s.content.includes('MES-Einführung'));
    const report = stillThere ? ATS : { ...ATS, keywords: { ...ATS.keywords, present_unsupported: [], present_unsupported_matches: {} } };
    // ADR-081 cl. 9 shape: the re-audit answered without a report (the producer did not run).
    if (opts.editedReportMissing) return json(r, { report: { document_id: CV_ID, status: 'failed', report: null, review_state: {} }, review_state: {} });
    return json(r, { report: { document_id: CV_ID, status: 'ready', report, review_state: {} }, review_state: {} });
  });
  await page.route('**/api/cv/generate', async (r) => {
    server.generates.push(JSON.parse(r.request().postData() ?? '{}'));
    return json(r, { cv_id: CV_ID, status: 'pending', expires_at: '2026-12-22T00:00:00Z' });
  });
  await page.route(`**/api/cv/${CV_ID}/sections/*/rewrite`, async (r) => {
    server.rewrites.push({ url: r.request().url(), body: JSON.parse(r.request().postData() ?? '{}') });
    return json(r, { suggestion: 'Vorschlag', withheld_count: 0 });
  });
  await page.route(`**/api/cv/${CV_ID}/sections/*`, async (r) => {
    if (r.request().method() !== 'PATCH') return r.fallback();
    const body = JSON.parse(r.request().postData() ?? '{}') as { content: string };
    server.patches.push({ url: r.request().url(), body });
    const id = r.request().url().split('/').pop()!;
    sections = sections.map((s) => (s.section_id === id ? { ...s, content: body.content, has_override: true } : s));
    return json(r, { html: CV_HTML, overrides_applied: [id], resolved_gaps: [] });
  });
  // Letter
  await page.route(`**/api/cover-letter/${CL_ID}/status`, (r) =>
    json(r, {
      cover_letter_id: CL_ID,
      status: 'ready',
      letter_data: LETTER_DATA,
      section_overrides: opts.letterBodyEdited ? { body: LETTER_DATA.body.paragraphs.join('\n\n') } : {},
      critic_report: LETTER_CRITIC,
      template: 'classic_german',
      document_language: 'de',
      signature_override: null,
      signature_effective: false,
      signature_available: false,
    }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/html`, (r) =>
    r.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `<html><body style="font-family:serif;padding:32px">${LETTER_DATA.body.paragraphs.map((p) => `<p>${p}</p>`).join('')}</body></html>`,
    }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/ats-report`, (r) =>
    json(r, { document_id: CL_ID, status: 'ready', report: { ...ATS, keywords: { ...ATS.keywords, present_unsupported: [] } }, review_state: {} }),
  );
  await page.route(`**/api/cover-letter/${CL_ID}/truthfulness-report`, (r) => json(r, { report: { ...TRUTH, document_kind: 'cover_letter' } }));
  await page.route(`**/api/cover-letter/${CL_ID}/critic-report`, (r) => json(r, { report: LETTER_CRITIC }));
  await page.route(`**/api/cover-letter/${CL_ID}/section`, async (r) => {
    server.patches.push({ url: r.request().url(), body: JSON.parse(r.request().postData() ?? '{}') });
    return json(r, { ok: true });
  });
  await page.route(`**/api/job/${JOB_ID}/gaps`, (r) => json(r, { unasked_requirements: [] }));
  return server;
}
