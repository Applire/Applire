// Copyright (C) 2024-2026 Tobias Rosenbaum
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

import { describe, expect, it } from "vitest";
import { formatPartialDate, isLegacyDate, parsePartialDate } from "../partialDate";

describe("parsePartialDate", () => {
  // H1.9 — no value is null, never "".
  it("treats null/undefined/empty as unknown (null)", () => {
    expect(parsePartialDate(null)).toBeNull();
    expect(parsePartialDate(undefined)).toBeNull();
    expect(parsePartialDate("")).toBeNull();
    expect(parsePartialDate("   ")).toBeNull();
  });

  it("parses a year-only value", () => {
    expect(parsePartialDate("2019")).toEqual({ year: 2019, month: null });
  });

  it("parses a year-month value", () => {
    expect(parsePartialDate("2020-03")).toEqual({ year: 2020, month: 3 });
  });

  it("parses a full year-month-day value by truncating to year+month", () => {
    expect(parsePartialDate("2020-03-15")).toEqual({ year: 2020, month: 3 });
  });

  // H1.12 — a value the picker cannot represent is "legacy", not silently
  // coerced or dropped.
  it.each(["Q3 2019", "2019/03", "March 2020", "circa 2018"])(
    "flags an unparseable legacy value: %s",
    (value) => {
      expect(parsePartialDate(value)).toBe("legacy");
      expect(isLegacyDate(value)).toBe(true);
    },
  );

  it("flags an out-of-range month as legacy rather than silently clamping it", () => {
    expect(parsePartialDate("2020-13")).toBe("legacy");
  });
});

describe("formatPartialDate", () => {
  it("emits YYYY when month is null", () => {
    expect(formatPartialDate({ year: 2019, month: null })).toBe("2019");
  });

  it("emits zero-padded YYYY-MM when month is known", () => {
    expect(formatPartialDate({ year: 2020, month: 3 })).toBe("2020-03");
    expect(formatPartialDate({ year: 2020, month: 12 })).toBe("2020-12");
  });
});
