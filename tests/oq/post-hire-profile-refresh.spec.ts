// Copyright (C) 2024-2026 Tobias Rosenbaum
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

import { test, expect, Page } from "@playwright/test";

/**
 * Post-Hire Profile Refresh — OQ Tests
 *
 * Covers:
 *  - /profile/upload renders the chooser when no action is set
 *  - "I started a new role" card navigates to ?action=add-role&source=manual
 *  - Add-role form renders, validates required fields, posts and navigates
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const OPEN_ROLE_ID = "11111111-1111-1111-1111-111111111111";

const PROFILE_RESPONSE = {
  id: "profile-id",
  profile: {
    work_experience: [
      {
        id: OPEN_ROLE_ID,
        company: "Acme",
        role: "Lead Engineer",
        start_date: "2023-01-01",
        end_date: null,
        responsibilities: [],
        achievements: [],
        technologies: [],
        role_aliases: [],
      },
    ],
    education: [],
    skills: [],
    certifications: [],
    languages: [],
    publications: [],
    volunteer_activities: [],
    personal_info: {},
    professional_summary: {},
  },
  completeness: 0.5,
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const ADD_ROLE_RESPONSE = {
  profile_id: "profile-id",
  new_role_id: "22222222-2222-2222-2222-222222222222",
  closed_role_ids: [OPEN_ROLE_ID],
  completeness_score: 0.6,
};

async function installMocks(page: Page) {
  await page.route("**/api/profile", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(PROFILE_RESPONSE),
      });
    } else {
      await route.fallback();
    }
  });

  await page.route("**/api/profile/roles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ADD_ROLE_RESPONSE),
    });
  });
}

test.describe("Post-Hire Profile Refresh", () => {
  test("chooser renders both cards when no action is set", async ({ page }) => {
    await installMocks(page);
    await page.goto("/profile/upload");

    // Both cards should be visible
    await expect(page.getByRole("heading", { name: /Update profile/i })).toBeVisible();
    await expect(page.getByText(/Upload a new CV/i)).toBeVisible();
    await expect(page.getByText(/I started a new role/i)).toBeVisible();
  });

  test("clicking 'I started a new role' navigates to the manual form", async ({ page }) => {
    await installMocks(page);
    await page.goto("/profile/upload");

    // Click the "I started a new role" card
    await page.getByText(/I started a new role/i).click();

    // Should navigate to /profile/upload?action=add-role&source=manual
    await expect(page).toHaveURL(/\/profile\/upload\?action=add-role&source=manual/);

    // Form should be visible: the AppTopbar detail title plus the step-1 heading.
    await expect(page.getByRole("heading", { name: /add role/i })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Your new position/i })).toBeVisible();
  });

  test("manual add-role: form validates, posts, and navigates to /profile", async ({ page }) => {
    await installMocks(page);
    await page.goto("/profile/upload?action=add-role&source=manual");

    // Step 1: Form should render
    await expect(page.getByText(/Your new position/i)).toBeVisible();
    const jobTitleInput = page.getByLabel(/Job title/i);
    const companyInput = page.getByLabel(/Company/i);
    const startDateInput = page.getByLabel(/Start date/i);
    const saveButton = page.getByRole("button", { name: /Save changes/i });

    await expect(jobTitleInput).toBeVisible();
    await expect(companyInput).toBeVisible();
    await expect(startDateInput).toBeVisible();

    // Save button should be disabled initially
    await expect(saveButton).toBeDisabled();

    // Step 2: With open role mocked, "Anything ending?" section should appear
    await expect(page.getByText(/Anything ending\?/i)).toBeVisible();
    await expect(page.getByText(/Acme/)).toBeVisible();
    await expect(page.getByText(/Lead Engineer/)).toBeVisible();

    // Fill required fields
    await jobTitleInput.fill("Director of QA");
    await companyInput.fill("Roche");
    await startDateInput.fill("2026-06-01");

    // Save button should be enabled
    await expect(saveButton).toBeEnabled();

    // Click Save — should POST and navigate to /profile
    await saveButton.click();

    // Should navigate to /profile
    await expect(page).toHaveURL(/\/profile$/);
  });

  test("save button is disabled without all required fields", async ({ page }) => {
    await installMocks(page);
    await page.goto("/profile/upload?action=add-role&source=manual");

    const saveButton = page.getByRole("button", { name: /Save changes/i });
    await expect(saveButton).toBeDisabled();

    // Fill only title
    await page.getByLabel(/Job title/i).fill("Director of QA");
    await expect(saveButton).toBeDisabled();

    // Add company
    await page.getByLabel(/Company/i).fill("Roche");
    await expect(saveButton).toBeDisabled();

    // Add start date — now it should be enabled
    await page.getByLabel(/Start date/i).fill("2026-06-01");
    await expect(saveButton).toBeEnabled();

    // Clear a required field — should disable again
    await page.getByLabel(/Job title/i).fill("");
    await expect(saveButton).toBeDisabled();
  });
});
