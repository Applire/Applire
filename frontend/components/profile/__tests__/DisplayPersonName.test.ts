// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// 789:181 — an all-caps CV name reads in title case on My Profile; mixed case is untouched.
import { describe, expect, it } from "vitest";
import { displayPersonName } from "../ProfileSectionCard";

describe("displayPersonName (789:181)", () => {
  it("title-cases an all-caps name, incl. umlauts, hyphens and apostrophes", () => {
    expect(displayPersonName("STEFAN BRANDT")).toBe("Stefan Brandt");
    expect(displayPersonName("JÖRG MÜLLER-LÜDENSCHEID")).toBe("Jörg Müller-Lüdenscheid");
    expect(displayPersonName("SEAN O'BRIEN")).toBe("Sean O'Brien");
  });

  it("leaves any mixed-case name exactly as stored", () => {
    expect(displayPersonName("Ronald McDonald")).toBe("Ronald McDonald");
    expect(displayPersonName("Anna van der Berg")).toBe("Anna van der Berg");
    expect(displayPersonName("Daniel Kovač")).toBe("Daniel Kovač");
    expect(displayPersonName("")).toBe("");
    expect(displayPersonName(null)).toBe(null);
  });
});
