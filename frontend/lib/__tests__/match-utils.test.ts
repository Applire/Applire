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
import { canonicalRequirementChips, gapCounts } from "../match-utils";

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

describe("canonicalRequirementChips", () => {
  // #111 (blind PQ F6): the JD-echo card must count/list the SAME fit-slice the
  // badges and score are computed from — never the raw JD lists next to ledger
  // math ("17 requirements detected" vs "24 direct matches").
  const ledger = [
    { concept: "Python", fit_weight: 1.0, sources: ["required"] },
    { concept: "Kubernetes", fit_weight: 1.0, sources: ["required", "keyword"] },
    { concept: "Terraform", fit_weight: 0.5, sources: ["nice_to_have"] },
    { concept: "agile", fit_weight: 0.0, sources: ["keyword"] }, // keyword-only: excluded
  ];

  it("derives chips from the ledger fit-slice when a ledger exists", () => {
    const chips = canonicalRequirementChips(ledger, ["raw-a", "raw-b"], ["raw-c"]);
    expect(chips.required).toEqual(["Python", "Kubernetes"]);
    expect(chips.niceToHave).toEqual(["Terraform"]);
  });

  it("total equals the badge math (A+B+C fit-slice size)", () => {
    const chips = canonicalRequirementChips(ledger, [], []);
    expect(chips.required.length + chips.niceToHave.length).toBe(3);
  });

  it("falls back to the raw JD lists for pre-ledger analyses", () => {
    const chips = canonicalRequirementChips(null, ["raw-a"], ["raw-c"]);
    expect(chips.required).toEqual(["raw-a"]);
    expect(chips.niceToHave).toEqual(["raw-c"]);
    expect(canonicalRequirementChips([], ["raw-a"], []).required).toEqual(["raw-a"]);
  });
});
