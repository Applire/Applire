// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Build-2 adversarial F-D (founder ruling R3-a): the "other language" keyword
// row moves a term the check compared by wording only. It may say the term
// was not checked across languages; it may never say the profile states it —
// the instrument never looked for the term in the other language.
import { describe, expect, it } from "vitest";
import de from "@/messages/de.json";
import en from "@/messages/en.json";

const rows = {
  de: [de.ats.checks["keyword-other-language"], de.ats.checkDetails["keyword-other-language"]],
  en: [en.ats.checks["keyword-other-language"], en.ats.checkDetails["keyword-other-language"]],
};

describe("keyword-other-language copy (R3-a)", () => {
  it("says the terms were not checked across languages", () => {
    for (const s of rows.de) expect(s).toMatch(/nicht sprachübergreifend geprüft/i);
    for (const s of rows.en) expect(s).toMatch(/not checked across languages/i);
  });

  it("never claims the profile states, names or holds the term", () => {
    for (const s of rows.de) expect(s).not.toMatch(/Profil nennt|nennt Dein Profil|Dein Profil (?:belegt|enthält) (?:diesen|diese)/);
    for (const s of rows.en) expect(s).not.toMatch(/profile states|states (?:this|these)|profile (?:names|has) (?:this|these)/i);
  });
});
