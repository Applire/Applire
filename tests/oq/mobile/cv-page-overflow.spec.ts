// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #786 L-3 (founder ruling 2026-10-22, option A) — mobile lane (390x844): the same
// card (in the review sheet since ADR-099 cl. 7), short title, buttons reachable, no horizontal overflow,
// and the preview keeps most of the screen. page.route() mocks — no backend.
import { test, expect } from "../../support/auth-fixture";
import { setupPageOverflowMocks, PO_FLOW_ID } from "../../support/cv-page-overflow-mocks";

test.describe("CV x+1 card (mobile 390x844)", () => {
  test("short title, tap to cut, no overflow, preview stays usable", async ({ page }, testInfo) => {
    const { posts } = await setupPageOverflowMocks(page);
    await page.goto(`/flow/${PO_FLOW_ID}/cv`);

    // The preview keeps most of the screen; the card is no longer above it.
    await expect(page.getByTestId("mobile-command-bar")).toBeVisible({ timeout: 15000 });
    const frameBox = (await page.getByTestId("cv-iframe").boundingBox())!;
    expect(frameBox.height).toBeGreaterThan(250);

    // ADR-099 cl. 7: the card lives in the review list — on the phone, the review sheet.
    await page.getByTestId("command-ats").tap();
    const sheet = page.getByTestId("command-sheet");
    const card = sheet.getByTestId("cv-page-overflow-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card.getByRole("heading")).toHaveText(/3 pages instead of 2$/, { useInnerText: true });
    await sheet.getByTestId("cv-page-overflow-cut").scrollIntoViewIfNeeded();
    await expect(sheet.getByTestId("cv-page-overflow-keep")).toBeInViewport();
    await expect(sheet.getByTestId("cv-page-overflow-cut")).toBeInViewport();

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.body.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);

    await page.screenshot({ path: testInfo.outputPath("cv-page-overflow-mobile.png") });

    await sheet.getByTestId("cv-page-overflow-cut").tap();
    await expect(sheet.getByTestId("cv-page-overflow-done")).toContainText("Cut to 2 pages");
    expect(posts).toEqual([{ choice: "cut" }]);
    await page.screenshot({ path: testInfo.outputPath("cv-page-overflow-mobile-cut.png") });
  });
});
