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
 * UAT-2b/2c (founder UAT 2026-11-08) — the lead-in above the answer ideas and
 * the idea buttons: the button reads the ask and falls back to the text; the
 * lead-in names exactly the concepts the backend sent (never one it did not),
 * quoted and joined naturally for 1, 2 and 3+ concepts in de and en.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChoiceLeadIn, choiceButtonLabel, hasAsks, quoteConceptList } from "../ChoiceLeadIn";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("quoteConceptList", () => {
  it.each([
    ["de", ["Prognosen"], "„Prognosen“"],
    ["de", ["Forecasting", "Kanban"], "„Forecasting“ oder „Kanban“"],
    ["de", ["A", "B", "C"], "„A“, „B“ oder „C“"],
    ["en", ["Forecasting"], "‘Forecasting’"],
    ["en", ["Forecasting", "Kanban"], "‘Forecasting’ or ‘Kanban’"],
    ["en", ["A", "B", "C"], "‘A’, ‘B’, or ‘C’"],
  ] as const)("[%s] %j", (locale, concepts, expected) => {
    expect(quoteConceptList([...concepts], locale)).toBe(expected);
  });
});

describe("choiceButtonLabel", () => {
  const choices = ["Ich habe X gemacht.", "Y habe ich bisher nicht, aber durch Z …"];
  it("shows the ask when there is one", () => {
    expect(choiceButtonLabel(choices, ["Hast du X gemacht?", null], 0)).toBe("Hast du X gemacht?");
  });
  it("falls back to the text per index, and without any asks", () => {
    expect(choiceButtonLabel(choices, ["Hast du X gemacht?", null], 1)).toBe(choices[1]);
    expect(choiceButtonLabel(choices, ["", "  "], 0)).toBe(choices[0]);
    expect(choiceButtonLabel(choices, null, 1)).toBe(choices[1]);
  });
  it("hasAsks only when one ask has content", () => {
    expect(hasAsks([null, " "])).toBe(false);
    expect(hasAsks(undefined)).toBe(false);
    expect(hasAsks([null, "Hast du?"])).toBe(true);
  });
});

describe("ChoiceLeadIn copy", () => {
  it.each([
    [
      "de",
      ["Prognosen"],
      "In deinem Profil habe ich keine direkte Nennung von „Prognosen“ gefunden. Vielleicht passt eine dieser Fragen – ein Klick übernimmt den Satz, du kannst ihn anpassen:",
    ],
    [
      "en",
      ["Forecasting"],
      "I found no direct mention of ‘Forecasting’ in your profile. Maybe one of these questions fits – one click copies the sentence, you can adjust it:",
    ],
    ["de", [], "Vielleicht passt eine dieser Fragen – ein Klick übernimmt den Satz, du kannst ihn anpassen:"],
    ["en", null, "Maybe one of these questions fits – one click copies the sentence, you can adjust it:"],
  ] as const)("[%s] %j", (locale, unmentioned, sentence) => {
    for (const ns of ["gaps", "interview"] as const) {
      const { unmount } = render(
        withIntl(<ChoiceLeadIn namespace={ns} asks={["Hast du?"]} unmentioned={unmentioned ? [...unmentioned] : null} />, locale),
      );
      expect(screen.getByTestId("choice-lead-in")).toHaveTextContent(sentence);
      unmount();
    }
  });

  it("names only the concepts it was sent", () => {
    render(withIntl(<ChoiceLeadIn namespace="gaps" asks={["Hast du?"]} unmentioned={["Kanban"]} />, "de"));
    const text = screen.getByTestId("choice-lead-in").textContent ?? "";
    expect(text).toContain("„Kanban“");
    expect(text).not.toContain("Prognosen");
  });

  it("keeps the old hint when no button carries an ask (statements, not questions)", () => {
    render(withIntl(<ChoiceLeadIn namespace="interview" asks={null} unmentioned={["Kanban"]} />, "de"));
    expect(screen.getByTestId("choice-lead-in")).toHaveTextContent("Wähle einen Startpunkt (editierbar):");
  });
});
