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
 * #763 OQ (mobile lane, ruling ADV-1) — the story receipt on /gaps fits a phone.
 */
import { ADMIN_USER, expect, test } from "../../support/auth-fixture";
import { FLOW_ID, LIABILITY, OFF_TOPIC, doneTurn, stubGaps } from "../gaps-receipt-fixtures";

test.use({ authUser: { ...ADMIN_USER, ui_language: "de" } });

test("#763 mobile: 'Geschichte erzählen' with nothing written shows the receipt inside the viewport", async ({ page }) => {
  await stubGaps(page, doneTurn("no_write"), "de");
  await page.goto(`/flow/${FLOW_ID}/gaps`);
  await page.getByTestId(`liability-tell-story-${LIABILITY}`).click();
  await page.getByTestId(`liability-answer-textarea-${LIABILITY}`).fill(OFF_TOPIC);
  await page.getByTestId(`liability-submit-${LIABILITY}`).click();
  const receipt = page.getByTestId(`liability-receipt-${LIABILITY}`);
  await expect(receipt).toContainText("wurde nichts übernommen");
  await receipt.scrollIntoViewIfNeeded();
  const box = await receipt.boundingBox();
  const vw = page.viewportSize()!.width;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(vw + 0.5);
  await expect(page.getByText("Geschichte hinzugefügt")).toHaveCount(0);
});

test("#763 mobile: the receipt stays after the completing turn's refresh drops the liability", async ({ page }) => {
  await stubGaps(page, doneTurn("no_write"), "de", { dropLiabilityAfterTurn: true });
  await page.goto(`/flow/${FLOW_ID}/gaps`);
  await page.getByTestId(`liability-tell-story-${LIABILITY}`).click();
  await page.getByTestId(`liability-answer-textarea-${LIABILITY}`).fill(OFF_TOPIC);
  await page.getByTestId(`liability-submit-${LIABILITY}`).click();
  await expect(page.getByTestId(`liability-tell-story-${LIABILITY}`)).toHaveCount(0);
  const receipt = page.getByTestId(`liability-receipt-${LIABILITY}`);
  await expect(receipt).toContainText("wurde nichts übernommen");
  await receipt.scrollIntoViewIfNeeded();
  const box = await receipt.boundingBox();
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 0.5);
});
