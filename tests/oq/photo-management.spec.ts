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
const TINY_JPEG_BASE64 =
  '/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U' +
  'HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN' +
  'DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy' +
  'MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAABgUEB' +
  '/8QAHhAAAQQDAQEBAAAAAAAAAAAAAQACAxEEBRIhMf/EABUBAQEAAAAAAAAAAAAAAAAAAAAB' +
  '/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8Amk2ta5rIKzG3HEQH9AAAASUVORK5CYII=';

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
});
