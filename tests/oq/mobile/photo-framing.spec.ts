// Copyright (C) 2026 Tobias Rosenbaum
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

/**
 * #781 OQ (mobile lane) — the framing dialog is a bottom sheet that fits the
 * phone viewport, its confirm button is reachable, and saving sends geometry.
 */
import { expect, test } from "../../support/auth-fixture";

// A real, decodable 16x9 JPEG.
const JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAJABADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDUooor1jzT/9k=",
  "base64",
);

test("#781 mobile: the framing sheet fits the viewport and saves geometry", async ({ page }) => {
  await page.route("**/api/profile", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "profile-1",
        profile: { personal_info: { name: "Max Mustermann", photo_url: "uploads/photo-uuid.jpg" } },
        completeness: 0.8,
        merge_conflicts: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }),
    }),
  );
  await page.route("**/api/profile/enrichment-history", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/profile/photo", (r) =>
    r.request().method() === "GET"
      ? r.fulfill({ status: 200, contentType: "image/jpeg", body: JPEG })
      : r.continue(),
  );
  let saved = false;
  await page.route("**/api/profile/photo/crop", (r) => {
    saved = true;
    return r.fulfill({ status: 200, contentType: "application/json", body: '{"photo_crop":null}' });
  });

  await page.goto("/profile");
  await page.getByTestId("photo-adjust").click();
  const dialog = page.getByTestId("photo-crop-dialog");
  await expect(dialog).toBeVisible();

  const vw = page.viewportSize()!.width;
  const vh = page.viewportSize()!.height;
  const stage = await page.getByTestId("photo-crop-stage").boundingBox();
  expect(stage).not.toBeNull();
  expect(stage!.x).toBeGreaterThanOrEqual(0);
  expect(stage!.x + stage!.width).toBeLessThanOrEqual(vw + 0.5);

  const confirm = page.getByTestId("photo-crop-confirm");
  await confirm.scrollIntoViewIfNeeded();
  const c = await confirm.boundingBox();
  expect(c!.y + c!.height).toBeLessThanOrEqual(vh + 0.5);
  await confirm.click();
  await expect(dialog).toHaveCount(0);
  expect(saved).toBe(true);
});
