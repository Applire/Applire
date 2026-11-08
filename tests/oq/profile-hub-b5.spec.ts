// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect } from "../support/auth-fixture";
import { openHub, routeMergeQuestion, shot } from "./profile-hub-b5-fixtures";

/**
 * Hazelnut build 5 — the profile hub and the review drawer in a real browser
 * (desktop). The mobile lane runs the same scenes in tests/oq/mobile/.
 */
test.describe("Hazelnut b5 — profile hub per class, conflict outcomes, entry merge", () => {
  test("class S is not rendered; the conflict, duplicate and its actions are", async ({ page }) => {
    await openHub(page);
    await expect(page.getByTestId("health-issue")).toHaveCount(3);
    await expect(page.getByTestId("health-panel")).not.toContainText("Datenabgleich");
    // UAT-1: the one card names the missing sections; without field_gaps the
    // enrich interview has nothing to ask, so there is no enrich button.
    await expect(page.getByTestId("health-panel")).toContainText(/Missing sections|Fehlende Abschnitte/);
    await expect(page.getByTestId("health-improve")).toHaveCount(0);
    await shot(page, "oq-hub-desktop");
  });

  test("U-2 + UAT-1: Resolve opens the clicked conflict; 'keep both' goes over the wire as an outcome", async ({ page }) => {
    const wire = await openHub(page);
    await page.getByTestId("health-issue").filter({ hasText: "Quality Lead" }).getByTestId("health-resolve").click();
    await expect(page.getByTestId("conflict-card")).toBeVisible();
    for (const o of ["keep_current", "take_new", "keep_both", "merge"]) {
      await expect(page.getByTestId(`conflict-outcome-${o}`)).toBeVisible();
    }
    expect(wire.find((w) => w.url.endsWith("/api/session/profile-review"))?.body).toEqual({
      issue_id: "conflict:c-list",
    });
    await shot(page, "oq-conflict-card-desktop");
    await page.getByTestId("conflict-outcome-keep_both").click();
    await expect(page.getByTestId("profile-review-done")).toBeVisible();
    expect(wire.find((w) => w.url.endsWith("/api/session/s1/message"))?.body).toEqual({ outcome: "keep_both" });
  });

  test("M-1c: two differing values are asked first; the pick goes over the wire", async ({ page }) => {
    const wire = await openHub(page);
    await routeMergeQuestion(page, wire);
    await page.getByTestId("health-issue").filter({ hasText: "Quality Lead" }).getByTestId("health-resolve").click();
    await page.getByTestId("conflict-outcome-merge").click();
    await expect(page.getByTestId("conflict-merge-question")).toBeVisible();
    await expect(page.getByTestId("conflict-merge-question-value-0")).toHaveText("18 %");
    await expect(page.getByTestId("conflict-merge-question-both")).toBeVisible();
    await expect(page.getByTestId("conflict-outcome-merge")).toHaveCount(0);
    await shot(page, "oq-merge-question-desktop");
    await page.getByTestId("conflict-merge-question-value-0").click();
    await expect(page.getByTestId("conflict-merge-text")).toHaveValue(
      "Rebuilt supplier selection for 4 sites and cut scrap by 18 %.",
    );
    expect(wire.filter((w) => w.url.endsWith("/merge-proposal")).map((w) => w.body)).toEqual([
      { conflict_id: "c-list" },
      { conflict_id: "c-list", choice: { kind: "value", value: "18 %", other: "12 %" } },
    ]);
  });

  test("U-9 → U-11: 'Yes, merge' opens the merge preview for the pair, target first", async ({ page }) => {
    const wire = await openHub(page);
    await page.getByTestId("health-duplicate-merge").click();
    const dialog = page.getByTestId("entry-merge-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId("entry-merge-dup-card")).toBeVisible();
    expect(wire.find((w) => w.url.endsWith("/api/profile/entries/merge"))?.body).toEqual({
      section: "work_experience",
      target_id: "we-1",
      source_id: "we-2",
      mode: "preview",
    });
    await shot(page, "oq-entry-merge-preview-desktop");
  });

  test("U-11: every work entry has a merge button; the picker lists the other entry", async ({ page }) => {
    await openHub(page);
    await expect(page.getByTestId("entry-merge-open")).toHaveCount(2);
    await page.getByTestId("entry-merge-open").nth(1).click();
    await expect(page.getByTestId("entry-merge-pick-option")).toHaveCount(1);
    await expect(page.getByTestId("entry-merge-pick-option")).toContainText("Quality Lead @ Nordwerk Logistik GmbH");
    await shot(page, "oq-entry-merge-pick-desktop");
  });

  test("U-7: 'Was it achieved?' — Yes asks what came out and sends it to the aspiration door", async ({ page }) => {
    const wire = await openHub(page);
    const card = page.getByTestId("health-issue-aspiration");
    await expect(card).toContainText("Reduce scrap below 2 % at all sites by end of 2019.");
    await page.getByTestId("health-aspiration-yes").click();
    await page.getByTestId("health-aspiration-outcome").fill("Scrap at 1.6 % by 11/2019.");
    await shot(page, "oq-u7-aspiration-desktop");
    await page.getByTestId("health-aspiration-save").click();
    await expect
      .poll(() => wire.find((w) => w.url.endsWith("/api/profile/aspirations/answer"))?.body)
      .toEqual({ issue_id: "aspiration:we-1:a1b2c3d4e5f6", achieved: true, outcome: "Scrap at 1.6 % by 11/2019." });
  });
});
