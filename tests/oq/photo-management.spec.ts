// tests/e2e/oq/photo-management.spec.ts
import { test, expect } from '../support/auth-fixture';
import path from 'path';

const TEST_FLOW_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff';
const TEST_JOB_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

const MOCK_FLOW_STATE_NO_CV = {
  job_id: TEST_JOB_ID,
  job_summary: { role_title: 'Software Engineer' },
  gap_summary: { match_score: 0.8 },
  cv_summary: null,
};

const MOCK_PROFILE_NO_PHOTO = {
  id: 'profile-1',
  profile: {
    personal_info: { name: 'Max Mustermann', photo_url: null },
  },
  completeness: 0.7,
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const MOCK_PROFILE_WITH_PHOTO = {
  id: 'profile-1',
  profile: {
    personal_info: {
      name: 'Max Mustermann',
      photo_url: 'uploads/photo-uuid.jpg',
    },
  },
  completeness: 0.8,
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

// 1×1 white JPEG (minimal valid JPEG bytes)
// #781: a real, decodable 16x9 JPEG — the framing dialog opens the picked file.
const TINY_JPEG_BASE64 =
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx' +
  '8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7' +
  'KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Oz' +
  'v/wAARCAAJABADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL' +
  '/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0f' +
  'AkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1' +
  'dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1N' +
  'XW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQF' +
  'BgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRob' +
  'HBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm' +
  'Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExc' +
  'bHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDUooor1jzT' +
  '/9k=';

test.describe('Sprint 14: Profile Photo — CV flow photo prompt', () => {
  test('shows photo prompt step when user has no photo, skipping goes to template select', async ({
    page,
  }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_FLOW_STATE_NO_CV),
      });
    });

    await page.route(`**/api/profile`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PROFILE_NO_PHOTO),
      });
    });

    await page.goto(`/flow/${TEST_FLOW_ID}/cv`);

    // Photo prompt step should appear
    await expect(page.getByText('Add a profile photo?')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Upload photo')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Skip for now')).toBeVisible({ timeout: 5_000 });

    // Clicking Skip advances to template select
    await page.getByText('Skip for now').click();
    await expect(page.getByText('Add a profile photo?')).not.toBeVisible();
    // TemplateSelector renders after skip
    await expect(page.getByTestId('cv-page')).toBeVisible();
  });

  test('skips photo prompt when user already has a photo', async ({ page }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_FLOW_STATE_NO_CV),
      });
    });

    await page.route(`**/api/profile`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PROFILE_WITH_PHOTO),
      });
    });

    await page.goto(`/flow/${TEST_FLOW_ID}/cv`);

    // Photo prompt step should NOT appear — goes straight to template select
    await expect(page.getByText('Add a profile photo?')).not.toBeVisible();
  });
});

test.describe('Sprint 14: Profile Photo — Profile page PhotoManager', () => {
  test('renders upload UI and consent checkbox when no photo exists', async ({
    page,
  }) => {
    await page.route(`**/api/profile`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PROFILE_NO_PHOTO),
      });
    });

    await page.route(`**/api/profile/enrichment-history`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    await page.goto('/profile');

    // PhotoManager empty state
    await expect(page.getByText('Upload a photo')).toBeVisible();
    const consentCheckbox = page.locator('#photo-consent');
    await expect(consentCheckbox).toBeVisible();
    await expect(consentCheckbox).not.toBeChecked();

    // Upload button disabled without consent
    const uploadBtn = page.getByRole('button', { name: 'Upload photo' });
    await expect(uploadBtn).toBeDisabled();

    // Tick consent — button becomes enabled
    await consentCheckbox.check();
    await expect(uploadBtn).toBeEnabled();
  });

  test('shows filled state with Replace/Delete buttons after upload', async ({
    page,
  }) => {
    await page.route(`**/api/profile`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PROFILE_NO_PHOTO),
      });
    });

    await page.route(`**/api/profile/enrichment-history`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    // Mock upload endpoint
    await page.route(`**/api/profile/photo*`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            photo_url: 'uploads/photo-new.jpg',
            consent_at: new Date().toISOString(),
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/profile');

    // Tick consent and trigger upload via file chooser
    await page.locator('#photo-consent').check();

    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: 'Upload photo' }).click(),
    ]);

    // Create a small JPEG buffer from base64
    const jpegBuffer = Buffer.from(TINY_JPEG_BASE64, 'base64');
    await fileChooser.setFiles({
      name: 'test-photo.jpg',
      mimeType: 'image/jpeg',
      buffer: jpegBuffer,
    });

    // #781 (ruling F-2): the picked file opens the framing dialog first; the
    // original is uploaded unmodified, WITH the chosen framing as geometry.
    await expect(page.getByTestId('photo-crop-dialog')).toBeVisible();
    const uploadReq = page.waitForRequest(
      (req) => req.method() === 'POST' && req.url().includes('/api/profile/photo'),
    );
    await page.getByTestId('photo-crop-confirm').click();
    const url = new URL((await uploadReq).url());
    expect(url.searchParams.get('consent')).toBe('true');
    for (const k of ['cx', 'cy', 'zoom']) expect(url.searchParams.get(k)).not.toBeNull();
    await expect(page.getByTestId('photo-crop-dialog')).toHaveCount(0);

    // After upload, filled state appears
    await expect(page.getByRole('button', { name: 'Replace' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete' })).toBeVisible();
    await expect(
      page.getByText('✓ Photo will appear in your Lebenslauf and Swiss CV templates')
    ).toBeVisible();
  });

  test('reverts to empty state after delete', async ({ page }) => {
    // Start with existing photo
    await page.route(`**/api/profile`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PROFILE_WITH_PHOTO),
      });
    });

    await page.route(`**/api/profile/enrichment-history`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    // Mock GET /photo to return the tiny JPEG
    await page.route(`**/api/profile/photo`, async (route) => {
      if (route.request().method() === 'GET') {
        const buf = Buffer.from(TINY_JPEG_BASE64, 'base64');
        await route.fulfill({
          status: 200,
          contentType: 'image/jpeg',
          body: buf,
        });
      } else if (route.request().method() === 'DELETE') {
        await route.fulfill({ status: 204 });
      } else {
        await route.continue();
      }
    });

    await page.goto('/profile');

    // Filled state visible
    await expect(page.getByRole('button', { name: 'Delete' })).toBeVisible();

    // Click Delete
    await page.getByRole('button', { name: 'Delete' }).click();

    // Reverts to empty state
    await expect(page.getByText('Upload a photo')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete' })).not.toBeVisible();
  });

  test('#781: "Adjust framing" re-frames the stored photo and saves only geometry', async ({ page }) => {
    await page.route(`**/api/profile`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PROFILE_WITH_PHOTO),
      });
    });
    await page.route(`**/api/profile/enrichment-history`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.route(`**/api/profile/photo`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, contentType: 'image/jpeg', body: Buffer.from(TINY_JPEG_BASE64, 'base64') });
      } else {
        await route.continue();
      }
    });
    let putBody: Record<string, number> | null = null;
    let postSeen = false;
    await page.route(`**/api/profile/photo/crop`, async (route) => {
      putBody = JSON.parse(route.request().postData() ?? 'null');
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ photo_crop: putBody }) });
    });
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/api/profile/photo')) postSeen = true;
    });

    await page.goto('/profile');
    await page.getByRole('button', { name: 'Adjust framing' }).click();
    await expect(page.getByTestId('photo-crop-dialog')).toBeVisible();
    await page.getByTestId('photo-crop-zoom').fill('2');
    await page.getByTestId('photo-crop-confirm').click();
    await expect(page.getByTestId('photo-crop-dialog')).toHaveCount(0);

    expect(putBody).not.toBeNull();
    expect(putBody!.zoom).toBeCloseTo(2, 2);
    expect(putBody!.cx).toBeGreaterThanOrEqual(0);
    expect(putBody!.cy).toBeGreaterThanOrEqual(0);
    // No file was re-uploaded: the stored original is untouched.
    expect(postSeen).toBe(false);
  });
});
