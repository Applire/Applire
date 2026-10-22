// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect } from "vitest";
import { avatarInitials, avatarLetter } from "../avatar";

describe("avatar", () => {
  it("builds two upper-case initials from an all-caps or normal name", () => {
    expect(avatarInitials("STEFAN BRANDT")).toBe("SB");
    expect(avatarInitials("Daniel Kovač")).toBe("DK");
    expect(avatarLetter("ANNA-LENA MÜLLER")).toBe("A");
  });
  it("falls back to the e-mail initial", () => {
    expect(avatarInitials(null, "anna@example.org")).toBe("A");
  });
});
