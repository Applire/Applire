// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Founder ruling UAT-1 (Hazelnut build 7, 2026-11-08) — the profile page shows
 * ONE completeness: the "Profile health" card, fed by /api/profile/health, with
 * a single enrich button ("Enrich Profile" / "Profil ergänzen"). No second badge, no second banner.
 *
 * Mocked API — no backend, no provider.
 */

import { test, expect } from "../support/auth-fixture";

const json = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const PROFILE = {
  id: "profile-1",
  profile: {
    personal_info: { name: "Lena Fischer" },
    professional_summary: null,
    work_experience: [
      { company: "Acme GmbH", title: "Software Engineer", start_date: "2020-01", end_date: "2023-12" },
    ],
    education: [],
    skills: [],
    languages: [],
    projects: [],
  },
  // Deliberately different from health.score: only health may be shown.
  completeness: 0.11,
  gaps: [],
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const HEALTH = {
  issues: [],
  completeness: { score: 0.72, gaps: ["education"], field_gaps: ["description"] },
};

test("profile page shows exactly one completeness number and one enrich button", async ({ page }) => {
  await page.route("**/api/profile", (r) => r.fulfill(json(PROFILE)));
  await page.route("**/api/profile/enrichment-history", (r) => r.fulfill(json([])));
  await page.route("**/api/profile/health", (r) => r.fulfill(json(HEALTH)));

  await page.goto("/profile");
  const card = page.getByTestId("health-panel");
  await expect(card).toBeVisible({ timeout: 30000 });

  await expect(page.getByTestId("health-completeness")).toHaveText(/72\s?%/);
  // exactly one percent-completeness label anywhere on the page, none from profile.completeness
  await expect(page.getByText(/Completeness/)).toHaveCount(1);
  await expect(page.getByText(/11\s?%/)).toHaveCount(0);
  await expect(page.getByRole("progressbar")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Enrich Profile" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Improve" })).toHaveCount(0);

  // Evidence screenshot only when asked for (UAT1_SCREENSHOT=<file.png>).
  if (process.env.UAT1_SCREENSHOT) await page.screenshot({ path: process.env.UAT1_SCREENSHOT });
});
