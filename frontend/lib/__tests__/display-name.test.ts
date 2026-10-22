// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect } from "vitest";
import { displayName, firstNameOf } from "../display-name";

describe("displayName", () => {
  it.each([
    ["STEFAN BRANDT", "Stefan Brandt"],
    ["DANIEL KOVAČ", "Daniel Kovač"],
    ["ANNA-LENA MÜLLER", "Anna-Lena Müller"],
    ["JEAN-PIERRE O'NEIL", "Jean-Pierre O'Neil"],
    ["JEAN-PIERRE O’NEIL", "Jean-Pierre O’Neil"],
    ["Stefan BRANDT", "Stefan Brandt"],
  ])("re-cases all-caps tokens: %s", (input, expected) => {
    expect(displayName(input)).toBe(expected);
  });

  it.each([
    "McDonald",
    "von Weber",
    "Daniel Kovač",
    "Anna Bauer",
    "Maria de la Cruz",
    "J. R. Smith",
    "Max A. Müller",
  ])("leaves mixed-case input exactly as given: %s", (input) => {
    expect(displayName(input)).toBe(input);
  });

  it("keeps a one-letter upper-case token and initials", () => {
    expect(displayName("MARIA J SCHMIDT")).toBe("Maria J Schmidt");
    expect(displayName("J.R. SMITH")).toBe("J.R. Smith");
  });

  it("returns null for empty input", () => {
    expect(displayName(null)).toBeNull();
    expect(displayName(undefined)).toBeNull();
    expect(displayName("   ")).toBeNull();
  });
});

describe("firstNameOf", () => {
  it("is the first token of the display name", () => {
    expect(firstNameOf("STEFAN BRANDT")).toBe("Stefan");
    expect(firstNameOf("ANNA-LENA MÜLLER")).toBe("Anna-Lena");
    expect(firstNameOf("Daniel Kovač")).toBe("Daniel");
    expect(firstNameOf("")).toBeNull();
    expect(firstNameOf(null)).toBeNull();
  });
});
