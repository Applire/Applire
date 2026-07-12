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
import { countWorkEntryGaps } from "../profile-gaps";

// #155 — the per-entry Enrich affordance must mirror the backend end_date
// presence predicate: missing end_date is a gap UNLESS is_current === true.
describe("countWorkEntryGaps", () => {
  it("counts a content gap when description AND achievements are both empty", () => {
    expect(
      countWorkEntryGaps({ description: null, end_date: "2023-01" }),
    ).toBe(1);
    expect(
      countWorkEntryGaps({ description: "Built things", end_date: "2023-01" }),
    ).toBe(0);
  });

  it("achievements satisfy the content gap — backend WorkEntry has no description field", () => {
    // #155 secondary bug: real (imported) entries never carry `description`,
    // so the old description-only check kept the Enrich button visible forever.
    expect(
      countWorkEntryGaps({
        achievements: ["Cut build times by 40%"],
        end_date: "2023-01",
      }),
    ).toBe(0);
    expect(countWorkEntryGaps({ achievements: [], end_date: "2023-01" })).toBe(1);
  });

  it("counts a missing end_date as a gap when the entry is not marked current", () => {
    expect(
      countWorkEntryGaps({ description: "Built things", end_date: null }),
    ).toBe(1);
    expect(
      countWorkEntryGaps({
        description: "Built things",
        end_date: null,
        is_current: false,
      }),
    ).toBe(1);
    expect(
      countWorkEntryGaps({ description: "Built things", end_date: "" }),
    ).toBe(1);
  });

  it("does NOT count a missing end_date when is_current === true", () => {
    expect(
      countWorkEntryGaps({
        description: "Built things",
        end_date: null,
        is_current: true,
      }),
    ).toBe(0);
  });

  it("a current entry with content has no gaps → Enrich button disappears", () => {
    expect(
      countWorkEntryGaps({
        description: "Backend development",
        end_date: null,
        is_current: true,
      }),
    ).toBe(0);
    expect(
      countWorkEntryGaps({
        achievements: ["Shipped the platform"],
        end_date: null,
        is_current: true,
      }),
    ).toBe(0);
  });
});
