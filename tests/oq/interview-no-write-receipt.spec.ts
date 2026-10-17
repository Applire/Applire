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
 * #763 OQ (desktop) — a turn that wrote nothing shows the receipt under the
 * candidate's answer, in the copy its reason selects; a turn that wrote
 * something shows none; the final turn's receipt survives the completion
 * screen. Uses page.route() mocks — no backend.
 */
import { ADMIN_USER, expect, test } from "../support/auth-fixture";
import { ANSWER, answerOnce, finalTurn, nextTurn, stubInterview } from "./interview-receipt-fixtures";
import { SETTINGS, json } from "./edit-tab-stub";

test.describe("#763 interview no-write receipt (desktop)", () => {
  test("no_write: the answer is echoed with the warning receipt before the next question", async ({ page }) => {
    await stubInterview(page, nextTurn("no_write"));
    await answerOnce(page);

    const turn = page.getByTestId("no-write-receipt-turn");
    await expect(turn).toBeVisible();
    await expect(turn).toContainText(ANSWER);
    await expect(turn.getByTestId("no-write-receipt")).toHaveText(
      "Nothing was recorded from what you said — say it again in one sentence, or enter it in your profile.",
    );
    await expect(page.getByTestId("interview-question")).toHaveText(/streaming pipeline/);
    // The history keeps the receipt under that answer once the turn block is gone.
    await expect(page.locator("details").getByTestId("no-write-receipt")).toHaveCount(1);
    // The receipt sits ABOVE the next question: answer → receipt → next question.
    const receiptBox = await turn.boundingBox();
    const questionBox = await page.getByTestId("question-card").boundingBox();
    expect(receiptBox && questionBox && receiptBox.y < questionBox.y).toBeTruthy();
  });

  test("already_known and question_only read their own sentence", async ({ page }) => {
    await stubInterview(page, nextTurn("no_write_already_known"));
    await answerOnce(page);
    await expect(page.getByTestId("no-write-receipt-turn").getByTestId("no-write-receipt")).toHaveText(
      "That is already in your profile, as stated.",
    );

    await page.unroute("**/api/session/*/message");
    await stubInterview(page, nextTurn("no_write_question_only"));
    await page.getByTestId("answer-textarea").fill("What do you mean by production here?");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("no-write-receipt-turn").getByTestId("no-write-receipt")).toHaveText(
      "Your question arrived — nothing was changed for it.",
    );
  });

  test("a turn that wrote something shows no receipt", async ({ page }) => {
    await stubInterview(page, { ...nextTurn(null), changes_applied: true });
    await answerOnce(page);
    await expect(page.getByTestId("interview-question")).toHaveText(/streaming pipeline/);
    await expect(page.getByTestId("no-write-receipt")).toHaveCount(0);
  });

  test("the final turn's receipt survives the completion screen", async ({ page }) => {
    await stubInterview(page, finalTurn("no_write"));
    await answerOnce(page);
    await expect(page.getByTestId("completion-screen")).toBeVisible();
    await expect(page.getByTestId("no-write-receipt-turn")).toContainText(ANSWER);
  });

  test.describe("German", () => {
    test.use({ authUser: { ...ADMIN_USER, ui_language: "de" } });
    test("no_write reads the German receipt", async ({ page }) => {
      // The shell's LocaleProvider applies GET /api/settings after /me; on the CI
      // harness the real stub admin answers an explicit "en", so the German lane
      // stubs it too (feedback: OQ green on bare `next dev` is not CI).
      await page.route("**/api/settings", (r) =>
        json(r, { ...SETTINGS, ui_language: "de", ui_language_explicit: true }),
      );
      await stubInterview(page, nextTurn("no_write"));
      await answerOnce(page);
      await expect(page.getByTestId("no-write-receipt-turn").getByTestId("no-write-receipt")).toHaveText(
        "Von dem, was du gesagt hast, wurde nichts übernommen — sag es noch einmal in einem Satz, oder trag es im Profil ein.",
      );
    });
  });
});
