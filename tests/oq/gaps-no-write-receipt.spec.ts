// Copyright (C) 2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

/**
 * #763 OQ (desktop, ruling ADV-1) — the /gaps composers: a story answer that
 * wrote nothing shows the receipt, never "Story added"; a turn that wrote keeps
 * the success line. page.route() mocks only.
 */
import { ADMIN_USER, expect, test } from "../support/auth-fixture";
import { CLUSTER, FLOW_ID, LIABILITY, OFF_TOPIC, doneTurn, stubGaps } from "./gaps-receipt-fixtures";

async function tellStory(page: import("@playwright/test").Page) {
  await page.goto(`/flow/${FLOW_ID}/gaps`);
  await page.getByTestId(`liability-tell-story-${LIABILITY}`).click();
  await page.getByTestId(`liability-answer-textarea-${LIABILITY}`).fill(OFF_TOPIC);
  await page.getByTestId(`liability-submit-${LIABILITY}`).click();
}

test.describe("#763 /gaps composers — no-write receipt (desktop)", () => {
  test("'Tell the story' that wrote nothing shows the receipt, not the success line", async ({ page }) => {
    await stubGaps(page, doneTurn("no_write"));
    await tellStory(page);
    const receipt = page.getByTestId(`liability-receipt-${LIABILITY}`);
    await expect(receipt).toHaveText(
      "Nothing was recorded from what you said — say it again in one sentence, or enter it in your profile.",
    );
    await expect(page.getByTestId(`liability-resolved-${LIABILITY}`)).toHaveCount(0);
    await expect(page.getByTestId(`liability-tell-story-${LIABILITY}`)).toBeVisible();
  });

  test("'Tell the story' that wrote keeps the success line", async ({ page }) => {
    await stubGaps(page, doneTurn(null, true));
    await tellStory(page);
    await expect(page.getByTestId(`liability-resolved-${LIABILITY}`)).toBeVisible();
    await expect(page.getByTestId(`liability-receipt-${LIABILITY}`)).toHaveCount(0);
  });

  test("the gap-click composer shows the receipt when the session ended with nothing written", async ({ page }) => {
    await stubGaps(page, doneTurn("no_write_already_known"));
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    const card = page.locator(`[data-testid="gap-cluster-card"][data-cluster-id="${CLUSTER}"]`);
    await card.click();
    await card.getByTestId("gap-answer-textarea").fill("I already listed Kubernetes in my skills.");
    await card.getByTestId("gap-submit-button").click();
    await expect(card.getByTestId("gap-receipt")).toHaveText("That is already in your profile, as stated.");
  });

  test.describe("German", () => {
    test.use({ authUser: { ...ADMIN_USER, ui_language: "de" } });
    test("the story receipt reads the approved German copy", async ({ page }) => {
      await stubGaps(page, doneTurn("no_write"), "de");
      await tellStory(page);
      await expect(page.getByTestId(`liability-receipt-${LIABILITY}`)).toHaveText(
        "Von dem, was du gesagt hast, wurde nichts übernommen — sag es noch einmal in einem Satz, oder trag es im Profil ein.",
      );
      await expect(page.getByText("Geschichte hinzugefügt")).toHaveCount(0);
    });
  });
});
