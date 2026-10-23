// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #786 L-3 (founder ruling 2026-10-22, option A) — mobile lane (390x844): the same
// card above the preview, short title, buttons reachable, no horizontal overflow,
// and the preview keeps most of the screen. page.route() mocks — no backend.
import { test, expect } from "../../support/auth-fixture";
import { setupPageOverflowMocks, PO_FLOW_ID } from "../../support/cv-page-overflow-mocks";

test.describe("CV x+1 card (mobile 390x844)", () => {
  test("short title, tap to cut, no overflow, preview stays usable", async ({ page }, testInfo) => {
    const { posts } = await setupPageOverflowMocks(page);
    await page.goto(`/flow/${PO_FLOW_ID}/cv`);

    const card = page.getByTestId("cv-page-overflow-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card.getByRole("heading")).toHaveText(/3 pages instead of 2$/, { useInnerText: true });
    await expect(page.getByTestId("cv-page-overflow-keep")).toBeInViewport();
    await expect(page.getByTestId("cv-page-overflow-cut")).toBeInViewport();

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.body.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);
    const frameBox = (await page.getByTestId("cv-iframe").boundingBox())!;
    expect(frameBox.height).toBeGreaterThan(250);

    await page.screenshot({ path: testInfo.outputPath("cv-page-overflow-mobile.png") });

    await page.getByTestId("cv-page-overflow-cut").tap();
    await expect(page.getByTestId("cv-page-overflow-done")).toContainText("Cut to 2 pages");
    expect(posts).toEqual([{ choice: "cut" }]);
    await page.screenshot({ path: testInfo.outputPath("cv-page-overflow-mobile-cut.png") });
  });
});
