// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";
import {
  formatDisplayDate,
  localizedLanguageLevel,
  localizedLanguageName,
} from "../profileDisplay";

describe("localizedLanguageName", () => {
  it("shows a German table name in English in the EN UI", () => {
    expect(localizedLanguageName("Deutsch", "en")).toBe("German");
    expect(localizedLanguageName("Englisch", "en")).toBe("English");
  });
  it("shows an English table name in German in the DE UI", () => {
    expect(localizedLanguageName("English", "de")).toBe("Englisch");
    expect(localizedLanguageName("german", "de")).toBe("Deutsch");
  });
  it("keeps a name already in the UI language", () => {
    expect(localizedLanguageName("Deutsch", "de")).toBe("Deutsch");
    expect(localizedLanguageName("English", "en")).toBe("English");
  });
  it("leaves unknown and parenthetical names unchanged", () => {
    expect(localizedLanguageName("Klingonisch", "en")).toBe("Klingonisch");
    expect(localizedLanguageName("Chinesisch (Mandarin)", "en")).toBe("Chinesisch (Mandarin)");
  });
});

describe("localizedLanguageLevel", () => {
  it("translates level words", () => {
    expect(localizedLanguageLevel("Muttersprache", "en")).toBe("Native");
    expect(localizedLanguageLevel("native", "de")).toBe("Muttersprache");
    expect(localizedLanguageLevel("Verhandlungssicher", "en")).toBe("Business fluent");
    expect(localizedLanguageLevel("Grundkenntnisse", "en")).toBe("Basic knowledge");
  });
  it("translates the known word of a compound and keeps the rest", () => {
    expect(localizedLanguageLevel("fließend, C1", "en")).toBe("fluent, C1");
    expect(localizedLanguageLevel("fluent, C1", "de")).toBe("fließend, C1");
  });
  it("passes CEFR codes and unknown phrases through", () => {
    expect(localizedLanguageLevel("C1", "en")).toBe("C1");
    expect(localizedLanguageLevel("B2", "de")).toBe("B2");
    expect(localizedLanguageLevel("conversational, rusty", "de")).toBe("conversational, rusty");
  });
});

describe("formatDisplayDate", () => {
  it("shows a year-only date as the year", () => {
    expect(formatDisplayDate("2022", "en")).toBe("2022");
    expect(formatDisplayDate("2022", "de")).toBe("2022");
  });
  it("shows a month date as localized month and year", () => {
    expect(formatDisplayDate("2021-03", "en")).toBe("Mar 2021");
    expect(formatDisplayDate("2021-03", "de")).toMatch(/^M(ä|a)rz? 2021$/);
  });
  it("shows a full date localized, without a time-zone shift", () => {
    expect(formatDisplayDate("2022-01-01", "en")).toBe("Jan 1, 2022");
    expect(formatDisplayDate("2022-01-01", "de")).toBe("01.01.2022");
  });
  it("leaves unparseable or empty values unchanged", () => {
    expect(formatDisplayDate("Q3 2019", "en")).toBe("Q3 2019");
    expect(formatDisplayDate("2021-13", "en")).toBe("2021-13");
    expect(formatDisplayDate(null, "en")).toBe("");
  });
});
