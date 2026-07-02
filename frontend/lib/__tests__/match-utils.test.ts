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

import { describe, it, expect } from "vitest";
import { gapCounts } from "../match-utils";

const NONE = new Set<string>();

describe("gapCounts", () => {
  it("splits a gap analysis into the four displayed counts", () => {
    const counts = gapCounts(
      { category_a: ["AWS", "Terraform"], category_b: ["Docker"], category_c: ["K8s", "SRE", "CI/CD"] },
      NONE,
    );
    expect(counts.directMatches).toBe(2);
    expect(counts.likelyMatches).toBe(1);
    expect(counts.gaps).toBe(3);
  });

  it("uses ONE canonical gap number for both the badge and the heading (F1: no contradiction)", () => {
    // The 'gaps identified' heading must equal the 'gaps to address' badge — a
    // partial/likely-match must never be silently counted as a gap.
    const counts = gapCounts(
      { category_a: [], category_b: ["Docker", "Ansible"], category_c: ["K8s", "SRE", "CI/CD"] },
      NONE,
    );
    // The single 'gaps' field is what both surfaces render.
    expect(counts.gaps).toBe(3); // category_c only — NOT 5 (would fold in the 2 likely matches)
  });

  it("still reports partials + gaps as itemsToAddress for flow gating (unchanged)", () => {
    const counts = gapCounts(
      { category_a: [], category_b: ["Docker", "Ansible"], category_c: ["K8s", "SRE", "CI/CD"] },
      NONE,
    );
    expect(counts.itemsToAddress).toBe(5); // 2 likely + 3 gaps — gates the section/CTA
  });

  it("excludes resolved gaps from the active counts", () => {
    const counts = gapCounts(
      { category_a: ["AWS"], category_b: ["Docker"], category_c: ["K8s", "SRE"] },
      new Set(["SRE", "Docker"]),
    );
    expect(counts.likelyMatches).toBe(0);
    expect(counts.gaps).toBe(1);
    expect(counts.itemsToAddress).toBe(1);
  });

  it("is tolerant of null / missing categories", () => {
    const counts = gapCounts(null, NONE);
    expect(counts).toEqual({ directMatches: 0, likelyMatches: 0, gaps: 0, itemsToAddress: 0 });
  });
});
