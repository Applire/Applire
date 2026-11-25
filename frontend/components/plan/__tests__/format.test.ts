// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";

import { formatDay, formatMonthBefore, formatMonthOf, formatPrice } from "../format";

describe("format", () => {
  it("formats prices by hand: DE 9,90 €, EN €9.90", () => {
    expect(formatPrice(990, "de")).toBe("9,90 €");
    expect(formatPrice(2490, "de")).toBe("24,90 €");
    expect(formatPrice(0, "de")).toBe("0,00 €");
    expect(formatPrice(990, "en")).toBe("€9.90");
    expect(formatPrice(1790, "en")).toBe("€17.90");
    expect(formatPrice(0, "en")).toBe("€0.00");
    expect(formatPrice(5, "en")).toBe("€0.05");
  });

  it("formats the renewal day as a local date", () => {
    expect(formatDay("2026-12-01", "de")).toBe("1. Dezember");
    expect(formatDay("2026-12-01", "en")).toBe("1 December");
  });

  it("names the month before and of the renewal date", () => {
    expect(formatMonthBefore("2026-12-01", "de")).toBe("November");
    expect(formatMonthBefore("2026-12-01", "en")).toBe("November");
    expect(formatMonthBefore("2027-01-01", "de")).toBe("Dezember");
    expect(formatMonthOf("2026-12-01", "de")).toBe("Dezember");
    expect(formatMonthOf("2026-12-01", "en")).toBe("December");
  });
});
