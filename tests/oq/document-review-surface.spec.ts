// tests/oq/document-review-surface.spec.ts
//
// E058 / ADR-081, re-cut by ADR-090 — the document review surface, driven in
// a real browser.
//
// Everything here needs a browser to be worth anything, which is why it is not
// a vitest:
//
//  * the #625 height claim. jsdom performs no layout, so the unit test can only
//    pin the MECHANISM (the preview column is not a scroll container and has no
//    sibling). Whether the preview actually keeps its height when the findings
//    grow is a LAYOUT question, and the honest way to answer it is to measure
//    the same element against a short and a long findings payload.
//  * ADR-081 cl. 6's visibility invariant, in the real panel — ADR-090 cl. 1
//    removed the overview/guided pair, so there is now exactly ONE layout to
//    exercise, not two.
//  * cl. 9's *unknown* state, produced the way it actually occurs: an endpoint
//    that answers 404 because the producer never wrote a report.
//  * ADR-090's own contract: *Show me where* highlighting real marks in the
//    preview iframe, *Take it out for me* / *Undo* / *add to my profile*
//    round-tripping through the review endpoints (Contract 2), and a finding
//    with no matched forms saying so instead of fabricating a place.
//
// Every backend call is stubbed with `page.route`, so this spec neither needs
// nor spends a provider call.
import { test, expect } from '../support/auth-fixture';
import { type Page } from '@playwright/test';

const FLOW_ID = 'e058e058-e058-e058-e058-e058e058e058';
const CV_ID = 'c058c058-c058-c058-c058-c058c058c058';
const JOB_ID = 'b058b058-b058-b058-b058-b058b058b058';
const CV_PAGE_URL = `/flow/${FLOW_ID}/cv`;

const FLOW_STATE = {
  job_id: JOB_ID,
  job_summary: { role_title: 'Produktionsleiter' },
  gap_summary: { match_score: 0.71, gaps: [], sections: [] },
  cv_summary: {
    cv_id: CV_ID,
    pdf_url: `http://localhost:8001/api/cv/${CV_ID}/pdf`,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  },
};

const CV_HTML = '<html><body><h1>Max Mustermann</h1><p>Produktionsleiter</p></body></html>';

/** A short findings payload: one group-1 finding, nothing else. */
const ATS_SHORT = {
  checks: [
    { id: 'contact-0', status: 'pass' },
    { id: 'headings-0', status: 'pass' },
  ],
  keywords: {
    present: ['SAP'],
    missing: [],
    missing_claimable: [],
    missing_honest_gap: [],
    present_unsupported: ['Kubernetes'],
    claimable_concepts: [],
  },
};

/**
 * A long findings payload. This is the #625 shape: a findings column long
 * enough to scroll. Under the old layout the preview shrank in proportion to
 * exactly this list.
 */
const ATS_LONG = {
  checks: [
    { id: 'contact-0', status: 'pass' },
    { id: 'headings-0', status: 'pass' },
    { id: 'page-length-0', status: 'fail', details: 'Drei Seiten statt zwei' },
    { id: 'dates-0', status: 'fail', details: 'Zwei Einträge ohne Enddatum' },
    { id: 'page-length-1', status: 'not_applicable' },
  ],
  keywords: {
    present: ['SAP'],
    missing: Array.from({ length: 24 }, (_, i) => `Begriff ${i + 1}`),
    missing_claimable: Array.from({ length: 12 }, (_, i) => `Claimable ${i + 1}`),
    missing_honest_gap: Array.from({ length: 12 }, (_, i) => `Honest ${i + 1}`),
    present_unsupported: Array.from({ length: 10 }, (_, i) => `Unsupported ${i + 1}`),
    claimable_concepts: [],
  },
};

const TRUTH_EMPTY = {
  version: '1',
  document_kind: 'cv',
  claims: [],
  counts: {},
  stated_limit: '',
};

const CRITIC_RAN = { ran: true, mount: 'cv', advisories: [], dropped_citations: 0 };

interface StubOptions {
  ats?: unknown | null;
  truth?: unknown | null;
  critic?: unknown | null;
  sections?: unknown | null;
  /** #786 / T-2: the status door's `review_degradation` (absent = not recorded). */
  degradation?: unknown | null;
}

/**
 * `null` for a report means "the producer never wrote one" and is served as a
 * 404 — the real shape of ADR-081 cl. 9's *unknown*, not a synthetic flag.
 */
async function stubBackend(page: Page, opts: StubOptions = {}) {
  const {
    ats = ATS_SHORT,
    truth = TRUTH_EMPTY,
    critic = CRITIC_RAN,
    sections = { sections: [], general_gaps: [] },
    degradation = null,
  } = opts;

  const json = async (route: import('@playwright/test').Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) => json(r, FLOW_STATE));
  await page.route('**/api/flow/*/advance', (r) => json(r, {}));
  await page.route(`**/api/cv/${CV_ID}/html`, (r) =>
    r.fulfill({ status: 200, contentType: 'text/html', body: CV_HTML }),
  );
  await page.route(`**/api/cv/${CV_ID}/status`, (r) =>
    json(r, {
      document_language: 'de',
      template: 'classic_german',
      // F-4b (founder ruling, 2026-09-11): no signature uploaded in this
      // fixture — the three-state control stays hidden.
      signature_override: null,
      signature_effective: false,
      signature_available: false,
      review_degradation: degradation,
    }),
  );
  await page.route('**/api/settings', (r) =>
    json(r, {
      default_color_profile_id: null,
      default_accent_hex: null,
      ui_language: 'en',
      ui_language_explicit: true,
      hide_predownload_notice: false,
      dismissed_explainers: [],
      target_cv_pages: 2,
    }),
  );

  const reportRoute = (suffix: string, body: unknown | null, key: string) =>
    page.route(`**/api/cv/${CV_ID}/${suffix}`, (r) =>
      body === null ? r.fulfill({ status: 404, body: '' }) : json(r, { [key]: body }),
    );
  await reportRoute('ats-report', ats, 'report');
  await reportRoute('truthfulness-report', truth, 'report');
  await reportRoute('critic-report', critic, 'report');

  await page.route(`**/api/cv/${CV_ID}/sections`, (r) =>
    sections === null ? r.fulfill({ status: 500, body: '' }) : json(r, sections),
  );
  await page.route(`**/api/application/**`, (r) => json(r, {}));
  // R2-10 (F1-2): the job's gap row — review group 3 shows ITS open count
  // (fit-weighted rows at status gap), never the ATS posting-keyword misses.
  await page.route('**/api/job/*/gaps', (r) =>
    json(r, {
      category_a: [], category_b: [], category_c: [],
      gap_clusters: [],
      keyword_ledger: [
        { concept: 'FastAPI', fit_weight: 1, status: 'gap', surface_forms: ['FastAPI'] },
        { concept: 'Apache Kafka', fit_weight: 1, status: 'gap', surface_forms: ['Apache Kafka'] },
        { concept: 'E-commerce', fit_weight: 0, status: 'gap', surface_forms: ['E-commerce'] },
        { concept: 'Python', fit_weight: 1, status: 'direct', surface_forms: ['Python'] },
      ],
    }),
  );
}

async function previewHeight(page: Page): Promise<number> {
  const box = await page.getByTestId('document-preview-column').boundingBox();
  expect(box).not.toBeNull();
  return Math.round(box!.height);
}

test.describe('E058 — the preview takes the height unconditionally (#625)', () => {
  test('a findings payload long enough to scroll does not shrink the preview', async ({ page }) => {
    // Short payload first.
    await stubBackend(page, { ats: ATS_SHORT });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    const short = await previewHeight(page);

    // Same viewport, same document, ~58 findings instead of 1.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await stubBackend(page, { ats: ATS_LONG });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    const long = await previewHeight(page);

    expect(short).toBeGreaterThan(200);
    // The whole of #625: this used to fall to ~45 px.
    expect(long).toBe(short);
  });

  test('the document-scope chrome is ONE region: no top bar, switch and exports in the panel', async ({
    page,
  }) => {
    await stubBackend(page);
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('refinement-sidebar')).toBeVisible({ timeout: 15_000 });

    await expect(page.locator('[data-testid="document-topbar"]')).toHaveCount(0);
    const header = page.getByTestId('sidebar-status-header');
    await expect(header.getByTestId('document-nav-cv')).toBeVisible();
    await expect(header.getByTestId('document-language-badge')).toBeVisible();
    await expect(page.getByTestId('sidebar-pinned-footer').getByTestId('document-download-btn')).toBeVisible();
  });

  test('the app nav is a rail on the document route and the full sidebar elsewhere', async ({ page }) => {
    await stubBackend(page);
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('app-sidebar')).toHaveAttribute('data-variant', 'rail');
    await page.getByTestId('app-sidebar-rail-expand').click();
    await expect(page.getByTestId('app-sidebar')).toHaveAttribute('data-variant', 'full');
  });
});

test.describe('E058/ADR-090 cl. 1 — ONE layout, no mode switch', () => {
  test('the open rows render without any mode interaction', async ({ page }) => {
    await stubBackend(page, { ats: ATS_LONG });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    // Group 1 has no `review-group-count-*` badge in the recut (ADR-090 cl. 1
    // replaced it with the card + list); its own non-zero signal is the list
    // itself, rendered without any click.
    await expect(page.locator('[data-testid^="review-item-g1-"]')).toHaveCount(10, { timeout: 15_000 });
    // ADR-099 cl. 8: groups 2 and 4 left the surface; no group toggle renders.
    for (const id of [2, 4]) await expect(page.getByTestId(`review-group-toggle-${id}`)).toHaveCount(0);
    // ADR-090 cl. 1: the overview/guided pair and its switch are retired.
    await expect(page.getByTestId('review-mode-switch')).toHaveCount(0);
    await expect(page.getByTestId('review-overview')).toHaveCount(0);
    await expect(page.getByTestId('review-guided')).toHaveCount(0);
  });

  test('the verdict sentence counts the group-1 rows it renders', async ({ page }) => {
    await stubBackend(page, { ats: ATS_LONG });
    await page.goto(CV_PAGE_URL);
    // The verdict renders immediately, before the reports arrive — wait for the
    // group-1 list to carry the loaded count, or this reads the empty-state
    // sentence and the assertion is a race rather than a check.
    await expect(page.locator('[data-testid^="review-item-g1-"]')).toHaveCount(10, { timeout: 15_000 });
    // ADR-099 cl. 3: the number counts the open rows plus the counted fallback
    // rows and unknown rows the panel renders — nothing it does not show.
    const stated = Number(await page.getByTestId('review-verdict').getAttribute('data-count'));
    const rendered =
      (await page.locator('[data-testid^="review-item-g1-"][data-status="open"]').count()) +
      (await page.locator('[data-testid^="review-fallback-"][data-counted="true"]').count()) +
      (await page.getByTestId('review-unknown').count());
    expect(rendered).toBe(stated);
    expect((await page.getByTestId('review-verdict').textContent()) ?? '').toContain(String(stated));
  });
});

test.describe('E058 — clause 9, a producer that did not run', () => {
  // ADR-099 cl. 2 / T-U: one counted "Not checked" row per producer that did not run.
  test('an Oracle that never wrote a report renders as unknown, never as zero', async ({ page }) => {
    await stubBackend(page, { truth: null });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    const box = page.getByTestId('review-unknown').filter({ hasText: 'Truthfulness Oracle' });
    await expect(box).toBeVisible();
    await expect(box.getByTestId('review-unknown-recheck')).toHaveText('Check now');
  });

  test('an outcome critic that did not run is a counted unknown row with a new-version action', async ({
    page,
  }) => {
    await stubBackend(page, { critic: { ran: false, advisories: [], dropped_citations: 0 } });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    const box = page.getByTestId('review-unknown').filter({ hasText: 'Coherence advisory' });
    await expect(box).toBeVisible();
    await expect(box).toHaveAttribute('data-resolver', 'regenerate');
  });

  test('#789 line L60 under T-U: an unanswered CV critic is never an all-clear', async ({ page }) => {
    const clean = { ...ATS_SHORT, keywords: { ...ATS_SHORT.keywords, present_unsupported: [] } };
    await stubBackend(page, { ats: clean, critic: { ran: true, reason: 'judgement_error', advisories: [], dropped_citations: 0 } });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-unknown').filter({ hasText: 'Not checked: Coherence advisory' })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId('review-verdict')).not.toContainText('ready to send');
  });

  test('with no reports at all the surface says so instead of heading itself with an all-clear', async ({
    page,
  }) => {
    await stubBackend(page, { ats: null, truth: null, critic: null, sections: null });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-unknown').first()).toBeVisible();
    await expect(page.getByTestId('review-verdict')).not.toContainText('ready to send');
    await expect(page.locator('[data-testid="review-passed-checks"]')).toHaveCount(0);
  });
});

test.describe('#786 / T-2 — a review step of the writing loop that did not run', () => {
  test('the note names the check above the verdict and offers a new version', async ({ page }) => {
    await stubBackend(page, {
      degradation: [{ check: 'cv_language', stage: 'review', reason: 'timeout', attempt: 1, count: 2 }],
    });
    await page.goto(CV_PAGE_URL);
    const note = page.getByTestId('review-degradation');
    await expect(note).toBeVisible({ timeout: 15_000 });
    await expect(note).toContainText('Language check: did not run — the AI provider did not answer in time.');
    await expect(note.getByTestId('review-degradation-regenerate')).toBeVisible();
  });

  test('a clean record renders no note', async ({ page }) => {
    await stubBackend(page, { degradation: [] });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-degradation')).toHaveCount(0);
  });
});

test.describe('ADR-099 cl. 8 — the trade (old group 2) is not on the surface', () => {
  test('a claimable term renders no row, no toggle and no handle', async ({ page }) => {
    await stubBackend(page, {
      ats: {
        checks: [{ id: 'contact-0', status: 'pass' }],
        keywords: {
          present: ['SAP'],
          missing: ['SAP PP'],
          missing_claimable: ['SAP PP'],
          missing_honest_gap: [],
          present_unsupported: [],
          claimable_concepts: [],
        },
      },
    });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-verdict')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-group-toggle-2')).toHaveCount(0);
    await expect(page.getByTestId('review-group2-trade')).toHaveCount(0);
    await expect(page.getByTestId('review-surface')).not.toContainText('SAP PP');
  });
});

test.describe('E058/ADR-090 — a stubbed end-to-end review flow', () => {
  // One finding the preview can locate (matched twice, once in a <p> and once
  // in an <li>) and two it cannot (no `present_unsupported_matches` entry —
  // Contract 3's "no data" case, ADR-090 cl. 2).
  const AI_GOV_TERM = 'IT Data & AI Governance';
  const AI_GOV_KEY = 'ats:it data & ai governance'; // ADR-070 cl. 1 fold, ported in lib/norm-quote.ts
  const STAKEHOLDER_TERM = 'Stakeholder Management';
  const STAKEHOLDER_KEY = 'ats:stakeholder management';
  const CLOUD_TERM = 'Cloud Architecture';

  const E2E_CV_HTML =
    '<html><body><h1>Max Mustermann</h1>' +
    '<p>Extensive experience in AI governance across global functions.</p>' +
    '<ul><li>Owned AI governance policy end to end.</li></ul>' +
    '</body></html>';

  const ATS_E2E_INITIAL = {
    checks: [{ id: 'contact-0', status: 'pass' }],
    keywords: {
      present: ['SAP'],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: [AI_GOV_TERM, STAKEHOLDER_TERM, CLOUD_TERM],
      present_unsupported_matches: { [AI_GOV_TERM]: [{ form: 'AI governance', stem: false }] },
      claimable_concepts: [],
    },
  };

  const ATS_E2E_NO_KEYWORD = {
    checks: [{ id: 'contact-0', status: 'pass' }],
    keywords: {
      present: ['SAP'],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: [STAKEHOLDER_TERM, CLOUD_TERM],
      present_unsupported_matches: {},
      claimable_concepts: [],
    },
  };

  const TAKEOUT_CHANGE = {
    section_id: 'experience',
    before:
      'Extensive experience in AI governance across global functions. Owned AI governance policy end to end.',
    after: 'Extensive experience across global functions. Owned policy end to end.',
  };

  test('locate, take out, undo, add to profile, an unmarkable finding, and the group-3 link', async ({
    page,
  }) => {
    await stubBackend(page, { ats: ATS_E2E_INITIAL });
    // Later registrations win (Playwright) — override the generic html/POST
    // stubs `stubBackend` set up with this flow's own fixtures.
    await page.route(`**/api/cv/${CV_ID}/html`, (r) =>
      r.fulfill({ status: 200, contentType: 'text/html', body: E2E_CV_HTML }),
    );
    await page.route(`**/api/cv/${CV_ID}/review/take-out`, (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          report: ATS_E2E_NO_KEYWORD,
          review_state: {
            walked_at: null,
            decisions: [
              {
                finding_key: AI_GOV_KEY,
                label: AI_GOV_TERM,
                action: 'taken_out',
                at: '2026-09-23T10:00:00Z',
                undo: { sections: [{ section_id: 'experience', before: TAKEOUT_CHANGE.before }] },
              },
            ],
          },
          changes: [TAKEOUT_CHANGE],
          still_listed: false,
        }),
      }),
    );
    await page.route(`**/api/cv/${CV_ID}/review/undo`, (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          report: ATS_E2E_INITIAL,
          review_state: { walked_at: null, decisions: [] },
        }),
      }),
    );
    await page.route(`**/api/cv/${CV_ID}/review/add-evidence`, (r) =>
      r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          report: ATS_E2E_NO_KEYWORD,
          review_state: {
            walked_at: null,
            decisions: [
              {
                finding_key: AI_GOV_KEY,
                label: AI_GOV_TERM,
                action: 'added',
                at: '2026-09-23T10:05:00Z',
                undo: null,
              },
            ],
          },
          testimony: { status: 'applied', changes: [{}] },
        }),
      }),
    );
    // #797 (ruling D2-5): *Stimmt* first asks whether the profile already holds
    // the wording; this finding's does not, so the testimony ask opens.
    await page.route(`**/api/cv/${CV_ID}/review/supported`, (r) =>
      r.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: { error: 'not_in_profile', message: 'x' } }),
      }),
    );
    await page.route(`**/api/cv/${CV_ID}/review/walked`, (r) =>
      r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ review_state: null }) }),
    );

    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });

    // (a) — the card names the document's own wording and how often it occurs.
    await expect(page.getByTestId('review-card-matched')).toHaveText(
      'Your document says “AI governance” in 2 places. Nothing in your profile backs it.',
      { timeout: 15_000 },
    );

    // (b) — Show me where: two marks in the preview, the stepper, and the
    // current mark moving on Next.
    await page.getByTestId('review-action-locate').click();
    const frame = page.frameLocator('iframe[data-testid="cv-iframe"]');
    await expect(frame.locator('mark[data-applire-hit]')).toHaveCount(2);
    await expect(page.getByTestId('review-locate-stepper')).toHaveText('Showing place 1 of 2');
    await expect(frame.locator('mark[data-applire-hit="0"][data-applire-current]')).toHaveCount(1);
    await page.getByTestId('review-locate-next').click();
    await expect(page.getByTestId('review-locate-stepper')).toHaveText('Showing place 2 of 2');
    await expect(frame.locator('mark[data-applire-hit="0"][data-applire-current]')).toHaveCount(0);
    await expect(frame.locator('mark[data-applire-hit="1"][data-applire-current]')).toHaveCount(1);

    // (c) — Take it out for me.
    await page.getByTestId('review-action-takeout').click();
    await expect(page.getByTestId('review-takeout-done')).toHaveText(
      'Taken out in 2 places. This is what changed:',
    );
    // D-3: the changed sentences, one unit each — struck, then the replacement.
    await expect(page.getByTestId('review-takeout-before')).toHaveText([
      'Extensive experience in AI governance across global functions.',
      'Owned AI governance policy end to end.',
    ]);
    await expect(page.getByTestId('review-takeout-after')).toHaveText([
      'Extensive experience across global functions.',
      'Owned policy end to end.',
    ]);
    await expect(page.getByTestId('review-action-undo')).toBeVisible();
    await expect(page.getByTestId(`review-item-g1-${AI_GOV_KEY}`)).toHaveAttribute('data-status', 'taken_out');
    await expect(
      page.getByTestId(`review-item-g1-${AI_GOV_KEY}`).getByTestId('review-row-status-taken_out'),
    ).toHaveText('Taken out');
    await expect(page.getByTestId('review-verdict')).toHaveText('2 points need you before you send.');

    // (d) — Undo restores the finding to open.
    await page.getByTestId('review-action-undo').click();
    await expect(page.getByTestId('review-card')).toHaveAttribute('data-finding-key', AI_GOV_KEY);
    await expect(page.getByTestId('review-card')).toHaveAttribute('data-status', 'open');
    await expect(page.getByTestId('review-verdict')).toHaveText('3 points need you before you send.');

    // (e) — Add to profile.
    await page.getByTestId('review-action-add').click();
    await page.getByTestId('review-add-text').fill('I led this AI governance policy myself, end to end.');
    await page.getByTestId('review-add-submit').click();
    await expect(page.getByTestId(`review-item-g1-${AI_GOV_KEY}`)).toHaveAttribute('data-status', 'added');
    await expect(
      page.getByTestId(`review-item-g1-${AI_GOV_KEY}`).getByTestId('review-row-status-added'),
    ).toHaveText('Added to profile');

    // (f) — a finding with no matches entry cannot be marked in the preview.
    await page.getByTestId(`review-item-g1-${STAKEHOLDER_KEY}`).click();
    await expect(page.getByTestId('review-locate-not-found')).toHaveText(
      'This place could not be marked in the preview.',
    );
    await expect(page.getByTestId('review-action-locate')).toHaveCount(0);

    // (g) — group 3 links out to the gap view (ruling C-1).
    await expect(page.getByTestId('review-group3-link')).toHaveAttribute('href', `/flow/${FLOW_ID}/gaps`);
    // R2-10: its number is the gaps page's — 2 open requirements, not the ATS misses.
    await expect(page.getByTestId('review-group-count-3')).toHaveText('2');
  });
});
