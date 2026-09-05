// tests/oq/document-review-surface.spec.ts
//
// E058 / ADR-081 — the document review surface, driven in a real browser.
//
// Everything here needs a browser to be worth anything, which is why it is not
// a vitest:
//
//  * the #625 height claim. jsdom performs no layout, so the unit test can only
//    pin the MECHANISM (the preview column is not a scroll container and has no
//    sibling). Whether the preview actually keeps its height when the findings
//    grow is a LAYOUT question, and the honest way to answer it is to measure
//    the same element against a short and a long findings payload.
//  * ADR-081 cl. 6's visibility invariant across the two modes, in the real
//    panel, with the real settings response deciding the mode.
//  * cl. 9's *unknown* state, produced the way it actually occurs: an endpoint
//    that answers 404 because the producer never wrote a report.
//
// Every backend call is stubbed with `page.route`, so this spec neither needs
// nor spends a provider call.
import { test, expect, type Page } from '@playwright/test';

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
  reviewMode?: 'auto' | 'overview' | 'guided';
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
    reviewMode = 'overview',
  } = opts;

  const json = async (route: import('@playwright/test').Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) => json(r, FLOW_STATE));
  await page.route('**/api/flow/*/advance', (r) => json(r, {}));
  await page.route(`**/api/cv/${CV_ID}/html`, (r) =>
    r.fulfill({ status: 200, contentType: 'text/html', body: CV_HTML }),
  );
  await page.route(`**/api/cv/${CV_ID}/status`, (r) =>
    json(r, { document_language: 'de', template: 'classic_german' }),
  );
  await page.route('**/api/settings', (r) =>
    json(r, {
      default_color_profile_id: null,
      default_accent_hex: null,
      ui_language: 'en',
      ui_language_explicit: true,
      hide_predownload_notice: false,
      target_cv_pages: 2,
      review_mode: reviewMode,
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

test.describe('E058 — clause 6, one test PER MODE', () => {
  // A shared assertion passes on the mode it was written against; SF-REVIEW.2's
  // mitigation cell says so explicitly. These two are deliberately separate.
  test('overview mode renders every non-zero group count', async ({ page }) => {
    await stubBackend(page, { ats: ATS_LONG, reviewMode: 'overview' });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-overview')).toBeVisible({ timeout: 15_000 });
    for (const id of [1, 2, 3, 4]) {
      await expect(page.getByTestId(`review-group-count-${id}`)).toBeVisible();
      await expect(page.getByTestId(`review-group-count-${id}`)).not.toHaveText('0');
    }
  });

  test('guided mode renders every non-zero group count', async ({ page }) => {
    await stubBackend(page, { ats: ATS_LONG, reviewMode: 'guided' });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-guided')).toBeVisible({ timeout: 15_000 });
    for (const id of [1, 2, 3, 4]) {
      await expect(page.getByTestId(`review-group-count-${id}`)).toBeVisible();
      await expect(page.getByTestId(`review-group-count-${id}`)).not.toHaveText('0');
    }
    // …and the escape from the queue is always one click away.
    await page.getByTestId('review-mode-switch').click();
    await expect(page.getByTestId('review-overview')).toBeVisible();
  });

  test('the verdict sentence counts the group-1 rows it renders', async ({ page }) => {
    await stubBackend(page, { ats: ATS_LONG, reviewMode: 'overview' });
    await page.goto(CV_PAGE_URL);
    // The verdict renders immediately, before the reports arrive — wait for the
    // group-1 badge to carry the loaded count, or this reads the empty-state
    // sentence and the assertion is a race rather than a check.
    await expect(page.getByTestId('review-group-count-1')).toHaveText('10', { timeout: 15_000 });
    const sentence = (await page.getByTestId('review-verdict').textContent()) ?? '';
    const stated = Number(sentence.match(/\d+/)?.[0]);
    const rendered = await page.locator('[data-testid^="review-item-g1-"]').count();
    expect(stated).toBe(10);
    expect(rendered).toBe(stated);
  });
});

test.describe('E058 — clause 9, a producer that did not run', () => {
  test('an Oracle that never wrote a report renders as unknown, never as zero', async ({ page }) => {
    await stubBackend(page, { truth: null, reviewMode: 'overview' });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-group-unknown-1')).toBeVisible();
    await expect(page.getByTestId('review-group-unknown-1')).toContainText('did not run');
  });

  test('an outcome critic that did not run keeps group 4 out of the passed-checks collapse', async ({
    page,
  }) => {
    await stubBackend(page, { critic: { ran: false, advisories: [], dropped_citations: 0 } });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-group-unknown-4')).toBeVisible();
  });

  test('with no reports at all the surface says so instead of heading itself with an all-clear', async ({
    page,
  }) => {
    await stubBackend(page, { ats: null, truth: null, critic: null, sections: null });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-surface')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-verdict')).toContainText('not checked');
    await expect(page.getByTestId('review-group-count-1')).toHaveAttribute('data-review-unknown', 'true');
    await expect(page.locator('[data-testid="review-passed-checks"]')).toHaveCount(0);
  });
});

test.describe('E058 — group 2 names the trade and offers nothing', () => {
  test('the trade and the three handles render, and the group has no action', async ({ page }) => {
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
      reviewMode: 'overview',
    });
    await page.goto(CV_PAGE_URL);
    await expect(page.getByTestId('review-group2-trade')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-group2-handle-pages')).toBeVisible();
    await expect(page.getByTestId('review-group2-handle-pin')).toBeVisible();
    await expect(page.getByTestId('review-group2-handle-regenerate')).toBeVisible();
    // The group's only control is its own collapse toggle.
    await expect(page.getByTestId('review-group-2').locator('button')).toHaveCount(1);
    await expect(page.getByTestId('review-group-2').locator('a')).toHaveCount(0);
  });
});
