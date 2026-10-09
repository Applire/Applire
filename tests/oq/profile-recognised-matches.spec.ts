// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect, ADMIN_USER } from "../support/auth-fixture";
import { type Page } from "@playwright/test";

/**
 * WP-V (Strawberry build 2) — #717 "Schon in deinem Profil" on the import
 * summary and #709/#716 "Auch bekannt als" chips on the profile editors
 * (ADR-046/063 amended 2026-10-07; founder rulings V-2 = A, V-3 = A).
 *
 * page.route() mocks only — no backend. That includes `/api/settings`: the
 * `LocaleProvider` applies `authUser.ui_language` first and then whatever
 * `/api/settings` serves, so an unmocked settings call lets the CI stack's real
 * stub admin (explicit "en" once any earlier spec has loaded a page in the
 * en-US browser) switch this German spec to English. Locally against a bare
 * `next dev` that call fails and the "de" stays, which hid it.
 */

const MATCHED = [
  { section: "languages", entity_id: "l-en", incoming: "English", existing: "Englisch", basis: "name_table" },
  { section: "skills", entity_id: "s-ml", incoming: "Machine Learning", existing: "Maschinelles Lernen", basis: "model" },
  { section: "work_experience", entity_id: "w-nov", incoming: "Novartis / Systemanalytiker", existing: "Novartis Diagnostics GmbH / System Analyst", basis: "model" },
  { section: "languages", entity_id: "l-de", incoming: "German", existing: "Deutsch", basis: "name_table" },
  { section: "education", entity_id: "e-uni", incoming: "Universität Leipzig / Diplom", existing: "Universität Leipzig / German Diploma", basis: "model" },
  { section: "skills", entity_id: "s-cv", incoming: "Computervalidierung", existing: "Computer System Validation", basis: "model" },
  { section: "work_experience", entity_id: "w-lims", incoming: "Labvantage / LIMS Consultant", existing: "Labvantage Solutions GmbH / LIMS Consultant", basis: "model" },
];

const UPLOAD_RESULT = {
  profile_id: "p1",
  status: "COMPLETE",
  completeness_score: 0.82,
  conflicts: [],
  enrichment_record_id: null,
  expires_at: new Date().toISOString(),
  merge_status: "applied",
  not_applied: [],
  matched: MATCHED,
};

const PROFILE = {
  id: "p1",
  profile: {
    personal_info: { name: "Jana Albrecht" },
    professional_summary: {},
    work_experience: [
      {
        id: "w-nov", company: "Novartis Diagnostics GmbH", role: "System Analyst",
        start_date: "2011-06", end_date: "2012-07", responsibilities: [], achievements: [],
        technologies: [], role_aliases: ["Systemanalytiker"], company_aliases: ["Novartis"],
      },
    ],
    education: [
      { id: "e-uni", institution: "Universität Leipzig", degree: "German Diploma", field: "Biology", degree_aliases: ["Diplom"] },
    ],
    skills: [
      { id: "s-ml", name: "Maschinelles Lernen", category: "technical", proficiency: "advanced", aliases: ["Machine Learning"] },
      { id: "s-py", name: "Python", category: "technical", proficiency: "expert" },
    ],
    languages: [{ id: "l-en", language: "Englisch", level: "C1", aliases: ["English"] }],
    certifications: [],
    publications: [],
    projects: [],
    volunteer_activities: [],
    signature_stories: [],
  },
  completeness: 0.82,
  stats: { positions: 1, projects: 0, certifications: 0, data_points: 0 },
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: "2026-10-07T10:00:00+00:00",
};

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function mockGermanSettings(page: Page) {
  await page.route("**/api/settings", (r) =>
    r.fulfill(json({ ui_language: "de", ui_language_explicit: true, dismissed_explainers: [] })),
  );
}

async function mockImport(page: Page, separated: string[]) {
  await mockGermanSettings(page);
  await page.route("**/api/profile/uploads", (r) => r.fulfill(json([])));
  await page.route("**/api/profile/import-jobs", (r) => r.fulfill(json({ import_id: "imp-1", status: "pending" }, 202)));
  await page.route("**/api/profile/import-jobs/imp-1", (r) =>
    r.fulfill(json({ import_id: "imp-1", status: "ready", error_code: null, result: UPLOAD_RESULT })),
  );
  await page.route("**/api/profile/matches/separate", async (r) => {
    separated.push(r.request().postData() ?? "");
    await r.fulfill(json(PROFILE));
  });
}

async function uploadOneCv(page: Page) {
  await page.goto("/profile/upload?action=upload");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "jana-linkedin.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 synthetic"),
  });
}

test.describe("#717 — recognised entries on the import summary", () => {
  test.use({ authUser: { ...ADMIN_USER, ui_language: "de" } });

  test("lists the pairs, folds after five, offers undo except for language-table pairs", async ({ page }) => {
    const separated: string[] = [];
    await mockImport(page, separated);
    await uploadOneCv(page);

    const card = page.getByTestId("recognised-matches");
    await expect(card).toBeVisible();
    await expect(card.getByText("Schon in deinem Profil (7)")).toBeVisible();
    await expect(card.getByTestId("recognised-match-row")).toHaveCount(5);
    await expect(card.getByTestId("recognised-match-table")).toHaveCount(2);
    await card.getByTestId("recognised-matches-show-all").click();
    await expect(card.getByTestId("recognised-match-row")).toHaveCount(7);

    const row = card.getByTestId("recognised-match-row").filter({ hasText: "Machine Learning" });
    await row.getByRole("button", { name: "Nicht dasselbe" }).click();
    await expect(row.getByTestId("recognised-match-done")).toHaveText(/Als eigener Eintrag hinzugefügt/);
    expect(JSON.parse(separated[0])).toEqual({ entity_id: "s-ml", incoming: "Machine Learning" });
  });
});

test.describe("#709/#716 — alternate names on the profile editors", () => {
  test.use({ authUser: { ...ADMIN_USER, ui_language: "de" } });

  test("shows removable chips and removes one through a section save", async ({ page }) => {
    let patched: unknown = null;
    await mockGermanSettings(page);
    await page.route("**/api/profile", (r) =>
      r.request().method() === "GET" ? r.fulfill(json(PROFILE)) : r.fallback(),
    );
    await page.route("**/api/profile/skills?**", async (r) => {
      patched = r.request().postDataJSON();
      await r.fulfill(json(PROFILE));
    });
    await page.goto("/profile");

    const skills = page.getByTestId("alias-chips-skills");
    await expect(skills).toContainText("Maschinelles Lernen — auch bekannt als");
    await expect(page.getByTestId("alias-chips-work_experience")).toContainText("Novartis");
    await expect(page.getByTestId("alias-chips-education")).toContainText("Diplom");
    await expect(page.getByTestId("alias-chips-languages")).toContainText("English");

    await skills.getByRole("button", { name: "„Machine Learning“ als weiteren Namen entfernen" }).click();
    await expect.poll(() => patched).not.toBeNull();
    expect(JSON.stringify(patched)).not.toContain("Machine Learning");
  });
});
