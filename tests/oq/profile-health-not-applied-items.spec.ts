import { test, expect } from "@playwright/test";

/**
 * #705 (founder UAT on the Nougat RC, 2026-09-15) — the not-retained overlay
 * must name WHICH items, per section, and "go to section" must land on the
 * NAMED section (never "one of the affected sections" chosen at random).
 *
 * Drives the real profile page + the real ProfileReviewDrawer through the
 * real i18n catalogs, mocking only the network boundary — the same pattern
 * `profile-health-conflict.spec.ts` (#604) established for the conflict card.
 */

const MOCK_PROFILE = {
  id: "profile-1",
  profile: {
    personal_info: { name: "Lena Fischer" },
    work_experience: [],
    education: [],
    skills: [],
    languages: [],
    projects: [],
  },
  completeness: 0.4,
  gaps: [],
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

// 23 items across five sections, sorted by section — the exact shape
// `services/profile/health.py::_not_applied_issue` (#705) sends.
const SECTIONS = ["education", "languages", "projects", "skills", "work_experience"];
const ITEMS = Array.from({ length: 23 }, (_, n) => {
  const section = SECTIONS[n % SECTIONS.length];
  return {
    section,
    label: `${section} entry ${n}`,
    reason: n % 2 === 0 ? "no_op_carried_entry" : "op_rejected",
  };
}).sort((a, b) => (a.section < b.section ? -1 : a.section > b.section ? 1 : a.label < b.label ? -1 : 1));

const HEALTH_WITH_NOT_APPLIED = {
  issues: [
    {
      id: "not_applied:rec-1",
      thread: "not_applied",
      profile_mismatch_severity: "review",
      summary:
        "23 items from your cv_upload did not reach your profile (…) — no change carried it; the change came back malformed and was dropped",
      field_ref: SECTIONS.slice().sort().join(", "),
      source_record_ref: "rec-1",
      not_applied_count: ITEMS.length,
      not_applied_source: "cv_upload",
      not_applied_reasons: ["no_op_carried_entry", "op_rejected"],
      not_applied_labels: ITEMS.slice(0, 3).map((i) => i.label),
      not_applied_items: ITEMS,
    },
  ],
  completeness: { score: 0.4, gaps: [], field_gaps: [] },
};

async function openProfile(page: import("@playwright/test").Page) {
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
      body: JSON.stringify(HEALTH_WITH_NOT_APPLIED),
    }),
  );
  // The review drawer's session start — gaps_total 0 lands straight on the
  // "issue + action" state (no conflicts to walk), exactly the merge-loss
  // case this receipt is for.
  await page.route("**/api/session/profile-review", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: "s1",
        first_question: "Nothing to review",
        gaps_total: 0,
        gaps_remaining: 0,
        choices: null,
      }),
    }),
  );
  await page.goto("/profile");
  await expect(page.getByTestId("health-issue").first()).toBeVisible({ timeout: 30000 });
}

test.describe("#705 — not-retained overlay names WHICH items, per section", () => {
  test("lists all groups with their labels and offers one action per section", async ({ page }) => {
    await openProfile(page);

    await page.getByTestId("health-resolve").first().click();
    await expect(page.getByTestId("profile-review-not-applied-groups")).toBeVisible();

    const groups = page.getByTestId("not-applied-group");
    await expect(groups).toHaveCount(5);

    const text = (await page.getByTestId("profile-review-not-applied-groups").innerText()).replace(
      /\s+/g,
      " ",
    );
    // Every section's own entries are named — not a 3-item-capped sentence.
    for (const section of SECTIONS) {
      expect(text).toContain(`${section} entry`);
    }
    // Old single generic action is gone — replaced by per-section ones.
    await expect(page.getByTestId("profile-review-action")).toHaveCount(0);
    await expect(page.getByTestId("profile-review-section-action")).toHaveCount(5);
  });

  test("the action lands on the NAMED section, not a random one, with a dismissible callout", async ({
    page,
  }) => {
    await openProfile(page);

    await page.getByTestId("health-resolve").first().click();
    await expect(page.getByTestId("profile-review-not-applied-groups")).toBeVisible();

    // Click the "skills" group's action specifically.
    const skillsGroup = page.getByTestId("not-applied-group").filter({ hasText: "Skills" });
    await skillsGroup.getByTestId("profile-review-section-action").click();

    // The drawer closes and the skills section is in view with the callout.
    await expect(page.getByTestId("profile-review-drawer")).toHaveCount(0);
    const skillsSection = page.locator("#section-skills");
    await expect(skillsSection.getByTestId("not-applied-callout")).toBeVisible();
    const calloutText = await skillsSection.getByTestId("not-applied-callout").innerText();
    expect(calloutText).toContain("skills entry");
    // …never on a different section.
    await expect(
      page.locator("#section-work_experience").getByTestId("not-applied-callout"),
    ).toHaveCount(0);

    await skillsSection.getByTestId("not-applied-callout-dismiss").click();
    await expect(skillsSection.getByTestId("not-applied-callout")).toHaveCount(0);
  });
});
