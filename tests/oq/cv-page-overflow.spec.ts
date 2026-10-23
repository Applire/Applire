// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #786 L-3 (founder ruling 2026-10-22, option A) — desktop lane: the amber x+1
// card sits above the CV preview, names what would go, and cut/keep are remembered
// and switchable. page.route() mocks — no backend.
import { test, expect } from "../support/auth-fixture";
import { setupPageOverflowMocks, PO_FLOW_ID } from "../support/cv-page-overflow-mocks";

test.describe("CV x+1 card (desktop)", () => {
  test("card above the preview; cut names what went; switch back", async ({ page }, testInfo) => {
    const { posts } = await setupPageOverflowMocks(page);
    await page.goto(`/flow/${PO_FLOW_ID}/cv`);

    const card = page.getByTestId("cv-page-overflow-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("Your CV needs 3 pages instead of 2 pages");
    await expect(card).toContainText("Older certifications: REFA-Grundschein (2013), Sicherheitsbeauftragter (2014)");

    // Above the preview, in the preview column.
    const iframe = page.getByTestId("cv-iframe");
    await expect(iframe).toBeVisible();
    const cardBox = (await card.boundingBox())!;
    const frameBox = (await iframe.boundingBox())!;
    expect(cardBox.y + cardBox.height).toBeLessThanOrEqual(frameBox.y + 1);
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
});
