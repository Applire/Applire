// tests/e2e/oq/cv-preview.spec.ts
import { test, expect } from '@playwright/test';

const TEST_FLOW_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const TEST_CV_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const TEST_JOB_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const CV_PAGE_URL = `/flow/${TEST_FLOW_ID}/cv`;

const MOCK_FLOW_STATE = {
  job_id: TEST_JOB_ID,
  job_summary: { role_title: 'Senior Software Engineer' },
  gap_summary: {
    match_score: 0.87,
    gaps: [{ id: 'gap-1', label: 'Cloud Experience' }],
    sections: [
      {
        section_id: 'intro',
        label: 'Introduction',
        content: 'Erfahrener Entwickler',
        has_override: false,
        gaps: [{ id: 'gap-1', label: 'Cloud Experience' }],
      },
    ],
  },
  cv_summary: {
    cv_id: TEST_CV_ID,
    pdf_url: `http://localhost:8001/api/cv/${TEST_CV_ID}/pdf`,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  },
};

const MOCK_CV_HTML = `<html><body>
  <h1>Max Mustermann</h1>
  <p>Senior Software Engineer</p>
</body></html>`;

test.describe('CV View — 70/30 layout rendering', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_FLOW_STATE),
      });
    });

    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: MOCK_CV_HTML,
      });
    });
  });

  test('iframe is present with non-empty srcdoc attribute', async ({ page }) => {
    const cspErrors: string[] = [];
    page.on('console', (msg) => {
      if (
        msg.type() === 'error' &&
        (msg.text().includes('Content-Security-Policy') ||
          msg.text().includes('frame-ancestors') ||
          msg.text().includes('X-Frame-Options'))
      ) {
        cspErrors.push(msg.text());
      }
    });

    await page.goto(CV_PAGE_URL);

    const iframe = page.locator('[data-testid="cv-iframe"]');
    await expect(iframe).toBeVisible({ timeout: 10_000 });

    const srcdoc = await iframe.getAttribute('srcdoc');
    expect(srcdoc).toBeTruthy();
    expect(srcdoc!.length).toBeGreaterThan(0);

    const sandbox = await iframe.getAttribute('sandbox');
    expect(sandbox).toBe('allow-same-origin');

    expect(cspErrors).toHaveLength(0);
  });

  test('iframe renders visible text content from CV HTML', async ({ page }) => {
    await page.goto(CV_PAGE_URL);

    const iframe = page.locator('[data-testid="cv-iframe"]');
    await expect(iframe).toBeVisible({ timeout: 10_000 });

    const frame = page.frameLocator('[data-testid="cv-iframe"]');
    await expect(frame.locator('h1')).toHaveText('Max Mustermann', { timeout: 5_000 });
  });

  test('refinement panel is visible with Actions tab', async ({ page }) => {
    await page.goto(CV_PAGE_URL);

    const panel = page.locator('[data-testid="refinement-panel"]');
    await expect(panel).toBeVisible({ timeout: 10_000 });

    // Page action bar should be present (signals CV preview phase is fully rendered)
    await expect(page.locator('[data-testid="tab-content"]')).toBeVisible();
    await expect(page.locator('[data-testid="cv-page-action-bar"]')).toBeVisible();
  });

  test('download button triggers PDF fetch', async ({ page }) => {
    let pdfFetched = false;
    await page.route(`**/api/cv/${TEST_CV_ID}/pdf`, async (route) => {
      pdfFetched = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        headers: {
          'Content-Disposition': `attachment; filename="lebenslauf-test.pdf"`,
        },
        body: Buffer.from('%PDF-1.4 mock content'),
      });
    });

    // The download is gated by the pre-download grounding review + attestation
    // (US147 / ADR-040): clicking download opens a modal that fetches the CV↔profile
    // diff first, and only the attestation confirm triggers the actual PDF fetch.
    await page.route(`**/api/cv/${TEST_CV_ID}/profile-diff`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], grounded: true }),
      });
    });

    await page.goto(CV_PAGE_URL);

    await expect(page.locator('[data-testid="cv-iframe"]')).toBeVisible({
      timeout: 10_000,
    });

    // Clicking download opens the attestation review (a nudge, not a gate) — no
    // immediate download. Attesting ("Yes, this reflects my experience") fires the PDF.
    await page.click('[data-testid="page-action-download"]');
    const attest = page.locator('[data-testid="what-changed-confirm"]');
    await expect(attest).toBeVisible({ timeout: 10_000 });

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      attest.click(),
    ]);

    expect(download).toBeTruthy();
    expect(pdfFetched).toBe(true);
  });
});
