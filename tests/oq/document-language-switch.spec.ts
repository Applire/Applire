// tests/oq/document-language-switch.spec.ts
//
// E054/US289 (ADR-038 amendment clause 6) — post-generation language switch:
// the CV page badges the document's PINNED language (JF-F-G2.2), and switching
// shows an explicit notice that NAMES the section-override loss (JF-F-G2.1)
// BEFORE persisting `language_override` and handing off to the existing
// regeneration path (same template, new GeneratedCV).
import { test, expect } from '@playwright/test';

const TEST_FLOW_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeee1';
const TEST_CV_ID = 'cccccccc-cccc-cccc-cccc-ccccccccccc1';
const TEST_JOB_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1';
const TEST_APP_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1';
const CV_PAGE_URL = `/flow/${TEST_FLOW_ID}/cv`;

const MOCK_FLOW_STATE = {
  job_id: TEST_JOB_ID,
  application_id: TEST_APP_ID,
  job_summary: { role_title: 'Senior Software Engineer' },
  gap_summary: {
    match_score: 0.87,
    gaps: [],
    sections: [
      {
        section_id: 'work',
        label: 'Berufserfahrung',
        content: 'Erfahrener Entwickler',
        has_override: true, // the notice must NAME this section's loss
        gaps: [],
      },
      {
        section_id: 'skills',
        label: 'Kenntnisse',
        content: 'TypeScript',
        has_override: false,
        gaps: [],
      },
    ],
  },
  cv_summary: {
    cv_id: TEST_CV_ID,
    pdf_url: `http://localhost:8001/api/cv/${TEST_CV_ID}/pdf`,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
    sections: [
      {
        section_id: 'work',
        label: 'Berufserfahrung',
        content: 'Erfahrener Entwickler',
        has_override: true,
        gaps: [],
      },
    ],
  },
};

const MOCK_CV_STATUS = {
  cv_id: TEST_CV_ID,
  status: 'ready',
  html_url: `http://localhost:8001/api/cv/${TEST_CV_ID}/html`,
  pdf_url: `http://localhost:8001/api/cv/${TEST_CV_ID}/pdf`,
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  template: 'executive',
  origin: 'pipeline',
  document_language: 'de',
};

test.describe('Document language switch (US289)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_FLOW_STATE),
      });
    });
    await page.route(`**/api/cv/${TEST_CV_ID}/status`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_CV_STATUS),
      });
    });
    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: '<html><body><h1>Max Mustermann</h1></body></html>',
      });
    });
  });

  test('top bar badges the pinned document language', async ({ page }) => {
    await page.goto(CV_PAGE_URL);
    const badge = page.locator('[data-testid="document-language-badge"]');
    await expect(badge).toBeVisible({ timeout: 10_000 });
    // Specs render in the settings-driven locale (default en) — bilingual match.
    await expect(badge).toHaveText(/Deutsch|German/);
  });

  test('switching opens the notice naming the overridden section, confirm writes the override then regenerates in the same template', async ({
    page,
  }) => {
    let patchBody: Record<string, unknown> | null = null;
    let generateBody: Record<string, unknown> | null = null;
    let patchDone = false;
    let patchBeforeGenerate = false;

    await page.route(`**/api/applications/${TEST_APP_ID}`, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON();
        patchDone = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: TEST_APP_ID, language_override: 'en' }),
        });
        return;
      }
      await route.fallback();
    });
    await page.route('**/api/cv/generate', async (route) => {
      generateBody = route.request().postDataJSON();
      patchBeforeGenerate = patchDone; // ordering: override persisted FIRST
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          cv_id: TEST_CV_ID,
          status: 'pending',
          html_url: '',
          pdf_url: '',
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        }),
      });
    });

    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // Open the Aktionen tab, where the switch lives.
    await page.click('[data-testid="sidebar-tab-actions"]');
    const switchEn = page.locator('[data-testid="doc-language-switch-en"]');
    await expect(switchEn).toBeVisible();
    // The current language is highlighted, not the other one.
    await expect(
      page.locator('[data-testid="doc-language-switch-de"]')
    ).toHaveAttribute('aria-pressed', 'true');

    await switchEn.click();

    // Explicit notice BEFORE anything happens — and it NAMES the overridden
    // section (JF-F-G2.1), not just "regenerates".
    const dialog = page.locator('[data-testid="doc-language-switch-dialog"]');
    await expect(dialog).toBeVisible();
    await expect(
      page.locator('[data-testid="doc-language-switch-override-loss"]')
    ).toContainText('Berufserfahrung');
    expect(patchBody).toBeNull(); // nothing written yet

    await page.click('[data-testid="doc-language-switch-confirm"]');

    // Override persisted, THEN the existing regeneration path — same template.
    await expect
      .poll(() => generateBody, { timeout: 10_000 })
      .not.toBeNull();
    expect(patchBody).toEqual({ language_override: 'en' });
    expect(patchBeforeGenerate).toBe(true);
    expect(generateBody).toMatchObject({ job_id: TEST_JOB_ID, template: 'executive' });
  });

  test('cancelling the notice writes nothing', async ({ page }) => {
    let patched = false;
    await page.route(`**/api/applications/${TEST_APP_ID}`, async (route) => {
      if (route.request().method() === 'PATCH') {
        patched = true;
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        return;
      }
      await route.fallback();
    });

    await page.goto(CV_PAGE_URL);
    await page.click('[data-testid="sidebar-tab-actions"]');
    await page.click('[data-testid="doc-language-switch-en"]');
    await expect(
      page.locator('[data-testid="doc-language-switch-dialog"]')
    ).toBeVisible();
    await page.click('[data-testid="doc-language-switch-cancel"]');
    await expect(
      page.locator('[data-testid="doc-language-switch-dialog"]')
    ).toBeHidden();
    expect(patched).toBe(false);
  });
});
