// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect } from "vitest";
import { formatVersion } from "../format-version";

describe("formatVersion", () => {
  it.each([
    ["0.44.0b0", "v0.44.0-beta"],
    ["1.0.0rc1", "v1.0.0-rc.1"],
    ["1.0.0a2", "v1.0.0-alpha.2"],
    ["0.45.0b2", "v0.45.0-beta.2"],
    ["v0.44.0-beta", "v0.44.0-beta"],
    ["0.44.0-beta", "v0.44.0-beta"],
    ["v1.0.0-rc.1", "v1.0.0-rc.1"],
    ["1.0.0", "v1.0.0"],
    ["v1.0.0", "v1.0.0"],
    ["1.0.0.dev3", "v1.0.0.dev3"],
  ])("%s -> %s", (input, expected) => {
    expect(formatVersion(input)).toBe(expected);
  });

  it("is empty for missing input", () => {
    expect(formatVersion(undefined)).toBe("");
    expect(formatVersion(null)).toBe("");
    expect(formatVersion("  ")).toBe("");
  });
});
