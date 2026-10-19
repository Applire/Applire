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
 * #763 OQ (mobile lane) — the receipt is visible on a phone viewport, inside
 * the viewport's width, above the next question.
 */
import { expect, test } from "../../support/auth-fixture";
import { ANSWER, answerOnce, nextTurn, stubInterview } from "../interview-receipt-fixtures";

test("#763 mobile: the no-write receipt fits the viewport above the next question", async ({ page }) => {
  await stubInterview(page, nextTurn("no_write"));
  await answerOnce(page);

  const turn = page.getByTestId("no-write-receipt-turn");
  await expect(turn).toBeVisible();
  await expect(turn).toContainText(ANSWER);
  const box = await turn.boundingBox();
  const vw = page.viewportSize()!.width;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(vw + 0.5);
  const q = await page.getByTestId("question-card").boundingBox();
  expect(q && box!.y < q.y).toBeTruthy();
});
