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
 * #763 — the copy selection for the interview's no-write receipt: each of the
 * three reasons the backend sends reads its own sentence in DE and EN, the
 * sentence matches the Health hub's clause copy (one wording on both
 * surfaces), and anything else renders nothing.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import { NoWriteReceipt, NoWriteReceiptLine, noWriteReceiptKey } from "../NoWriteReceipt";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("noWriteReceiptKey", () => {
  it.each([
    ["no_write", "nothingRecorded"],
    ["no_write_already_known", "alreadyKnown"],
    ["no_write_question_only", "questionOnly"],
  ])("%s → %s", (reason, key) => {
    expect(noWriteReceiptKey(reason)).toBe(key);
  });

  it.each([null, undefined, "", "op_rejected", "no_op_carried_entry", "confirmation_discarded"])(
    "%s → no receipt",
    (reason) => {
      expect(noWriteReceiptKey(reason as string | null | undefined)).toBeNull();
    },
  );
});

describe("NoWriteReceiptLine copy", () => {
  it.each([
    ["en", "no_write", "Nothing was recorded from what you said — say it again in one sentence, or enter it in your profile."],
    ["en", "no_write_already_known", "That is already in your profile, as stated."],
    ["en", "no_write_question_only", "Your question arrived — nothing was changed for it."],
    ["de", "no_write", "Von dem, was du gesagt hast, wurde nichts übernommen — sag es noch einmal in einem Satz, oder trag es im Profil ein."],
    ["de", "no_write_already_known", "Das steht so schon in deinem Profil."],
    ["de", "no_write_question_only", "Deine Frage ist angekommen — dazu wurde nichts geändert."],
  ] as const)("[%s] %s", (locale, reason, sentence) => {
    render(withIntl(<NoWriteReceiptLine reason={reason} />, locale));
    expect(screen.getByTestId("no-write-receipt")).toHaveTextContent(sentence);
  });

  it("renders nothing for a reason that is not a receipt", () => {
    const { container } = render(withIntl(<NoWriteReceiptLine reason="op_rejected" />, "en"));
    expect(container).toBeEmptyDOMElement();
  });

  it("only the turn's own receipt is a live region", () => {
    render(withIntl(<NoWriteReceipt answer="I ran Airflow." reason="no_write" />, "en"));
    expect(screen.getByRole("status")).toHaveTextContent("Nothing was recorded");
    render(withIntl(<NoWriteReceiptLine reason="no_write" />, "en"));
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });
});

describe("one wording on both surfaces (#763: the Health hub's copies)", () => {
  // The Health hub composes clause-shaped copies; the interview reads them as
  // sentences. Same words, sentence-cased and closed with a full stop.
  const asSentence = (clause: string) => clause.charAt(0).toUpperCase() + clause.slice(1) + ".";
  it.each([
    ["no_write", "nothingRecorded"],
    ["no_write_already_known", "alreadyKnown"],
    ["no_write_question_only", "questionOnly"],
  ] as const)("%s", (reason, key) => {
    for (const cat of [de, en]) {
      const health = (cat.health.notAppliedReason as Record<string, string>)[reason];
      expect((cat.interview.noWriteReceipt as Record<string, string>)[key]).toBe(asSentence(health));
    }
  });
});
