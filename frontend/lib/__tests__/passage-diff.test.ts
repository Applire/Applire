// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

import { describe, it, expect } from "vitest";
import { changedPassages, passageUnits } from "../passage-diff";

// D-3: the delivery run's shapes — a multi-paragraph letter body and a
// newline-joined bullet list — where only one sentence / one bullet changed.
const LETTER_BEFORE =
  "Dear team,\n\nI built European e-commerce platforms for ten years. I also ran the payment stack.\n\nI led settlement for merchants across five markets. Happy to talk.";
const LETTER_AFTER =
  "Dear team,\n\nI built platforms for ten years. I also ran the payment stack.\n\nI led settlement for merchants across five markets. Happy to talk.";

describe("changedPassages (D-3)", () => {
  it("returns only the changed sentence of a long letter body, the rest elided", () => {
    const hunks = changedPassages(LETTER_BEFORE, LETTER_AFTER);
    expect(hunks).toEqual([
      {
        before: ["I built European e-commerce platforms for ten years."],
        after: ["I built platforms for ten years."],
        elidedBefore: true,
        elidedAfter: true,
      },
    ]);
  });

  it("a removed bullet is a pure removal; the other bullets are not shown", () => {
    const before = "Built the shipment-tracking backend.\nServed ~40,000 customers.\nOwned the AWS deployment.";
    const after = "Built the shipment-tracking backend.\nOwned the AWS deployment.";
    expect(changedPassages(before, after)).toEqual([
      { before: ["Served ~40,000 customers."], after: [], elidedBefore: true, elidedAfter: true },
    ]);
  });

  it("keeps two separate changes as two hunks", () => {
    const hunks = changedPassages("A one. B two. C three. D four.", "A one. B 2. C three. D 4.");
    expect(hunks.map((h) => [h.before, h.after])).toEqual([
      [["B two."], ["B 2."]],
      [["D four."], ["D 4."]],
    ]);
    expect(hunks[1].elidedAfter).toBe(false);
  });

  it("identical texts have no hunks; whitespace-only differences are not changes", () => {
    expect(changedPassages("A.  B.", "A. B.")).toEqual([]);
  });

  it("splits on newlines (CRLF too) and sentence ends", () => {
    expect(passageUnits("One. Two!\r\nThree?\n\nFour")).toEqual(["One.", "Two!", "Three?", "Four"]);
  });
});
