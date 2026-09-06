import { test, expect } from "@playwright/test";

/**
 * #604 — E2E coverage for the Health hub's conflict card.
 *
 * The collector line this closes: #626 changed what that card renders, and
 * shipped on unit + vitest evidence only. The three profile OQ specs
 * (profile-enrichment, post-hire-profile-refresh, photo-management) all carry
 * `merge_conflicts: []` and never mock `/api/profile/health` at all, so no
 * browser has ever rendered a conflict — the one surface where the reported
 * defect was visible.
 *
 * These specs drive the real page through the real i18n catalogs and assert on
 * what a user reads, not on the API shape.
 */

const MOCK_PROFILE = {
  id: "profile-1",
  profile: {
    personal_info: { name: "Max Mustermann" },
    professional_summary: "Backend engineer with a decade in logistics.",
    work_experience: [
      {
        id: "we-1",
        company: "Acme GmbH",
        title: "Senior Software Engineer",
        start_date: "2020-01",
        end_date: "2023-12",
        description: "Owned the dispatch platform.",
      },
    ],
  },
  completeness: 0.82,
  gaps: [],
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

// The contract `services/profile/health.py` actually returns for a conflict
// whose `entity_id` resolves — the structured fields #626 added.
const HEALTH_WITH_ENTITY_CONFLICT = {
  issues: [
    {
      id: "conflict:c1",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary:
        "Senior Software Engineer @ Acme GmbH: work_experience.end_date: '2023-12' vs '2024-03'",
      field_ref: "end_date",
      source_record_ref: "cv_upload",
      entity_label: "Senior Software Engineer @ Acme GmbH",
      section: "work_experience",
      field: "end_date",
      existing_value_display: "2023-12",
      incoming_value_display: "2024-03",
      existing_source: null,
      incoming_source: "cv_upload",
    },
  ],
  completeness: { score: 0.82, gaps: [], field_gaps: [] },
};

// A profile-level dispute: `entity_id` was never set (#218 — professional_summary
// has no entity), so `entity_label` is legitimately null.
const HEALTH_WITH_PROFILE_LEVEL_CONFLICT = {
  issues: [
    {
      id: "conflict:c2",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary: "professional_summary.en: 'Old summary' vs 'New summary'",
      field_ref: "en",
      source_record_ref: "cv_upload",
      entity_label: null,
      section: "professional_summary",
      field: "en",
      existing_value_display: "Old summary",
      incoming_value_display: "New summary",
      existing_source: null,
      incoming_source: "cv_upload",
    },
  ],
  completeness: { score: 0.9, gaps: [], field_gaps: [] },
};

async function openProfileWith(
  page: import("@playwright/test").Page,
  health: unknown,
) {
  await page.route("**/api/profile", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_PROFILE),
    }),
  );
  await page.route("**/api/profile/enrichment-history", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/profile/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(health),
    }),
  );
  await page.goto("/profile");
  // The conflict card itself is the load signal — waiting on it avoids
  // networkidle, which never settles (Google Fonts in the root layout).
  await expect(page.getByTestId("health-issue-conflict")).toBeVisible({
    timeout: 30000,
  });
}

test.describe("#604 — Health hub conflict card", () => {
  test("names the entry the dispute belongs to, never the raw section.field shape", async ({
    page,
  }) => {
    await openProfileWith(page, HEALTH_WITH_ENTITY_CONFLICT);

    const card = page.getByTestId("health-issue-conflict");
    const text = (await card.innerText()).replace(/\s+/g, " ");

    // The reported defect (#626): the card named the field but never WHICH job.
    expect(text).toContain("Senior Software Engineer @ Acme GmbH");
    // …and said it in raw backend prose. Both must be gone from the surface.
    expect(text).not.toContain("work_experience.end_date");
    expect(text).not.toContain("end_date");
    expect(text).toContain("End date");

    // Both sides of the dispute are readable, with the incoming side's
    // provenance resolved to a human label rather than the raw `cv_upload` key.
    expect(text).toContain("2023-12");
    expect(text).toContain("2024-03");
    expect(text).not.toContain("cv_upload");

    // The card is actionable — the whole point of surfacing it.
    await expect(page.getByTestId("health-resolve").first()).toBeVisible();
  });

  test("falls back to a field-only heading when the conflict has no entity", async ({
    page,
  }) => {
    await openProfileWith(page, HEALTH_WITH_PROFILE_LEVEL_CONFLICT);

    const text = (
      await page.getByTestId("health-issue-conflict").innerText()
    ).replace(/\s+/g, " ");

    expect(text).toContain("Summary (English)");
    expect(text).toContain("Old summary");
    expect(text).toContain("New summary");
    // A missing entity must read as absent, never as a rendered placeholder.
    expect(text).not.toMatch(/null|undefined/);
    expect(text).not.toContain("professional_summary");
  });

  test("renders the German catalog, not English fallbacks", async ({ page }) => {
    // `LocaleProvider` reads the active UI language from GET /api/settings —
    // there is no cookie or query-param switch.
    await page.route("**/api/settings", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ui_language: "de", ui_language_explicit: true, dismissed_explainers: [] }),
      }),
    );
    await openProfileWith(page, HEALTH_WITH_ENTITY_CONFLICT);

    const text = (
      await page.getByTestId("health-issue-conflict").innerText()
    ).replace(/\s+/g, " ");
    expect(text).toContain("Enddatum");
    expect(text).toContain("Aktueller Wert");
  });
});
