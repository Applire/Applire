// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #786 L-3 (founder ruling 2026-10-22, option A) — desktop lane: the amber x+1
// card (in the review list since ADR-099 cl. 7), names what would go, and cut/keep are remembered
// and switchable. page.route() mocks — no backend.
import { test, expect } from "../support/auth-fixture";
import { setupPageOverflowMocks, PO_FLOW_ID } from "../support/cv-page-overflow-mocks";

test.describe("CV x+1 card (desktop)", () => {
  test("card in the review list; cut names what went; switch back", async ({ page }, testInfo) => {
    const { posts } = await setupPageOverflowMocks(page);
    await page.goto(`/flow/${PO_FLOW_ID}/cv`);

    const card = page.getByTestId("cv-page-overflow-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("Your CV needs 3 pages instead of 2 pages");
    await expect(card).toContainText("Older certifications: REFA-Grundschein (2013), Sicherheitsbeauftragter (2014)");

    // ADR-099 cl. 7: page length has ONE place — this card, in the review list
    // (it moved there from above the preview); the preview keeps its column.
    await expect(page.getByTestId("review-surface").getByTestId("cv-page-overflow-card")).toBeVisible();
    await expect(page.getByTestId("cv-page-overflow-card")).toHaveCount(1);
    const frameBox = (await page.getByTestId("cv-iframe").boundingBox())!;
    expect(frameBox.height).toBeGreaterThan(300);

    await page.screenshot({ path: testInfo.outputPath("cv-page-overflow-desktop.png") });

    await page.getByTestId("cv-page-overflow-cut").click();
    const done = page.getByTestId("cv-page-overflow-done");
    await expect(done).toContainText("Cut to 2 pages — removed: REFA-Grundschein (2013), Sicherheitsbeauftragter (2014), Kosmetik-Verpackungen, Lebensmittelrecht.");
    expect(posts).toEqual([{ choice: "cut" }]);
    await page.screenshot({ path: testInfo.outputPath("cv-page-overflow-desktop-cut.png") });

    await page.getByTestId("cv-page-overflow-switch").click();
    await expect(done).toContainText("3 pages — your choice.");
    expect(posts).toEqual([{ choice: "cut" }, { choice: "keep" }]);

    // Remembered: a reload shows the done line, not the card.
    await page.reload();
    await expect(page.getByTestId("cv-page-overflow-done")).toHaveAttribute("data-choice", "keep", { timeout: 15000 });
    await expect(page.getByTestId("cv-page-overflow-card")).toHaveCount(0);
  });

  // R-1: nothing left for Applire to cut on its own — the person picks the roles.
  test("nothing to cut: pick two roles, preview says 2 pages, apply, done line, undo", async ({ page }) => {
    const { posts, previews } = await setupPageOverflowMocks(page, { scenario: "nothing-to-cut" });
    await page.goto(`/flow/${PO_FLOW_ID}/cv`);

    const card = page.getByTestId("cv-page-overflow-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("cv-page-overflow-body")).toContainText("does not cut without you");
    // Two answers, each with its sub-line; no automatic "Cut to" button.
    await expect(card.getByTestId("cv-page-overflow-keep")).toContainText("Keep 3 pages");
    await expect(card.getByTestId("cv-page-overflow-keep-sub")).toBeVisible();
    await expect(card.getByTestId("cv-page-overflow-pick")).toContainText("Shorten roles yourself");
    await expect(card.getByTestId("cv-page-overflow-cut")).toHaveCount(0);

    await card.getByTestId("cv-page-overflow-pick").click();
    const menu = card.getByTestId("cv-page-overflow-pick-menu");
    await expect(menu.getByTestId("cv-page-overflow-role")).toHaveCount(3);
    await expect(card.getByTestId("cv-page-overflow-apply")).toBeDisabled();

    await menu.getByTestId("cv-page-overflow-role").nth(0).getByRole("checkbox").check();
    await menu.getByTestId("cv-page-overflow-role").nth(1).getByRole("checkbox").check();
    // One debounced request for the final selection (800 ms after the last tick).
    const preview = card.getByTestId("cv-page-overflow-preview");
    await expect(preview).toContainText("With this selection: 2 pages", { timeout: 10000 });
    await expect(preview).toHaveAttribute("data-fits", "true");
    expect(previews).toEqual([{ roles: [2, 3] }]);
    await expect(card.getByTestId("cv-page-overflow-apply")).toHaveText("Shorten these 2 roles");

    await card.getByTestId("cv-page-overflow-apply").click();
    const done = page.getByTestId("cv-page-overflow-done");
    await expect(done).toContainText(
      "Shortened to 2 pages – title, company and dates only: Leiter Logistik (Nordwind Logistik GmbH), Teamleiter Qualität (Kranich Systems AG).",
    );
    expect(posts).toEqual([{ choice: "cut", roles: [2, 3] }]);
    await expect(page.getByTestId("cv-page-overflow-card")).toHaveCount(0);

    // Undo: back to the full length, the card's done line says it is the person's choice.
    await page.getByTestId("cv-page-overflow-switch").click();
    await expect(done).toContainText("3 pages — your choice.");
    expect(posts).toEqual([{ choice: "cut", roles: [2, 3] }, { choice: "keep" }]);
    // …and the roles can still be shortened after all.
    await expect(page.getByTestId("cv-page-overflow-kept-pick")).toBeVisible();
  });
});
