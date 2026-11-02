// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect } from "../../support/auth-fixture";
import { openHub, routeMergeQuestion, shot } from "../profile-hub-b5-fixtures";

/**
 * Hazelnut build 5 — the same hub scenes on a phone viewport: no drag handle
 * (the merge button is the non-drag path), the conflict card's four outcomes
 * fit the width, the merge dialog opens from the hub's "Ja".
 */
test.describe("Hazelnut b5 — profile hub on a phone", () => {
  test("conflict outcomes fit the viewport and send the structured outcome", async ({ page }) => {
    const wire = await openHub(page);
    await page.getByTestId("health-issue").filter({ hasText: "Quality Lead" }).getByTestId("health-resolve").click();
    const card = page.getByTestId("conflict-card");
    await expect(card).toBeVisible();
    const box = await card.boundingBox();
    expect(box && box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    await shot(page, "oq-conflict-card-mobile");
    await page.getByTestId("conflict-outcome-take_new").click();
    await expect(page.getByTestId("profile-review-done")).toBeVisible();
    expect(wire.find((w) => w.url.endsWith("/api/session/s1/message"))?.body).toEqual({ outcome: "take_new" });
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
    const box = await page.getByTestId("conflict-merge-question").boundingBox();
    expect(box && box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    await shot(page, "oq-merge-question-mobile");
    await page.getByTestId("conflict-merge-question-value-0").click();
    await expect(page.getByTestId("conflict-merge-text")).toHaveValue(
      "Rebuilt supplier selection for 4 sites and cut scrap by 18 %.",
    );
    expect(wire.filter((w) => w.url.endsWith("/merge-proposal")).map((w) => w.body)).toEqual([
      { conflict_id: "c-list" },
      { conflict_id: "c-list", choice: { kind: "value", value: "18 %", other: "12 %" } },
    ]);
  });

  test("no drag handle on a phone; the merge button and the hub's 'Ja' open the dialog", async ({ page }) => {
    await openHub(page);
    await expect(page.getByTestId("entry-merge-handle").first()).toBeHidden();
    await expect(page.getByTestId("entry-merge-open")).toHaveCount(2);
    await page.getByTestId("health-duplicate-merge").click();
    await expect(page.getByTestId("entry-merge-dialog")).toBeVisible();
    await shot(page, "oq-entry-merge-preview-mobile");
  });

  test("U-7: 'No, it stays a goal' submits directly on a phone", async ({ page }) => {
    const wire = await openHub(page);
    await page.getByTestId("health-issue-aspiration").scrollIntoViewIfNeeded();
    await shot(page, "oq-u7-aspiration-mobile");
    await page.getByTestId("health-aspiration-no").click();
    await expect
      .poll(() => wire.find((w) => w.url.endsWith("/api/profile/aspirations/answer"))?.body)
      .toEqual({ issue_id: "aspiration:we-1:a1b2c3d4e5f6", achieved: false });
  });
});
