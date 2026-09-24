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
import {
  allClusterMembers,
  canonicalRequirementChips,
  cardTone,
  clusterCoverageCounts,
  clusterView,
  gapCounts,
  isGapRefusalCode,
  normMember,
  scoreToPercent,
  type GapCluster,
} from "../match-utils";

describe("gapCounts", () => {
  it("splits a gap analysis into the four displayed counts", () => {
    const counts = gapCounts(
      { category_a: ["AWS", "Terraform"], category_b: ["Docker"], category_c: ["K8s", "SRE", "CI/CD"] },
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
    );
    // The single 'gaps' field is what both surfaces render.
    expect(counts.gaps).toBe(3); // category_c only — NOT 5 (would fold in the 2 likely matches)
  });

  it("still reports partials + gaps as itemsToAddress for flow gating (unchanged)", () => {
    const counts = gapCounts(
      { category_a: [], category_b: ["Docker", "Ansible"], category_c: ["K8s", "SRE", "CI/CD"] },
    );
    expect(counts.itemsToAddress).toBe(5); // 2 likely + 3 gaps — gates the section/CTA
  });

  it("counts the server's lists as they are — an answered concept leaves them on the recomputed row, not in the client (ADR-089 clause 8)", () => {
    // Before: the page subtracted a client-side set of resolved CLUSTER IDS
    // from these CONCEPT lists — it never matched, and it vanished on reload.
    const before = gapCounts({ category_a: ["AWS"], category_b: ["Docker"], category_c: ["K8s", "SRE"] });
    const after = gapCounts({ category_a: ["AWS", "SRE"], category_b: ["Docker"], category_c: ["K8s"] });
    expect(before).toEqual({ directMatches: 1, likelyMatches: 1, gaps: 2, itemsToAddress: 3 });
    expect(after).toEqual({ directMatches: 2, likelyMatches: 1, gaps: 1, itemsToAddress: 2 });
    expect(gapCounts.length).toBe(1); // no second, client-side argument
  });

  it("is tolerant of null / missing categories", () => {
    const counts = gapCounts(null);
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

describe("scoreToPercent", () => {
  // #675 / ruling B-1 (2026-09-20) — a recorded denial is no longer clamped, so
  // an analysis in which every weighted requirement is `denied` genuinely
  // publishes 0.0. The gaps screen's four reads were truthiness checks and
  // displayed the previous, higher percentage instead.
  it("renders a genuine zero as 0, never as the fallback", () => {
    expect(scoreToPercent(0, 64)).toBe(0);
  });

  it("falls back only when no score exists at all", () => {
    expect(scoreToPercent(null, 64)).toBe(64);
    expect(scoreToPercent(undefined, 64)).toBe(64);
  });

  it("rounds a [0, 1] fraction to a whole percentage", () => {
    expect(scoreToPercent(0.6292, 0)).toBe(63);
    expect(scoreToPercent(0.6404, 0)).toBe(64);
    expect(scoreToPercent(1, 0)).toBe(100);
  });
});

// ---------------------------------------------------------------------------
// ADR-089 — the per-cluster coverage record
// ---------------------------------------------------------------------------

function cluster(over: Partial<GapCluster> = {}): GapCluster {
  return {
    id: "cl-1",
    label: "Container platform",
    category: "C",
    gaps: ["Kubernetes", "Terraform"],
    jd_skills: ["Kubernetes"],
    jd_context: "",
    outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
    coverage: "open",
    budget_remaining: 2,
    member_statuses: [
      { member: "Kubernetes", status: "gap" },
      { member: "Terraform", status: "gap" },
    ],
    ...over,
  };
}

type S = "covered" | "partial" | "gap" | "declined";
const statuses = (pairs: [string, S][]) => pairs.map(([member, status]) => ({ member, status }));

describe("allClusterMembers (TS mirror of gap_coverage.all_members)", () => {
  it("reads open + covered + declined, in that order", () => {
    const c = cluster({
      gaps: ["Terraform"],
      outcome: { asked: 1, covered: ["Kubernetes"], declined: ["Helm"], session_ids: ["s1"] },
    });
    expect(allClusterMembers(c)).toEqual(["Terraform", "Kubernetes", "Helm"]);
  });

  it("dedupes by the ledger normaliser (strip + casefold, ß → ss), keeping the first spelling", () => {
    const c = cluster({
      gaps: [" Kubernetes ", "Straße"],
      outcome: { asked: 1, covered: ["kubernetes", "STRASSE"], declined: [], session_ids: [] },
    });
    expect(allClusterMembers(c)).toEqual([" Kubernetes ", "Straße"]);
    expect(normMember(" Straße ")).toBe(normMember("strasse"));
  });

  it("is tolerant of a legacy row without an outcome", () => {
    const legacy = { gaps: ["RAG"] } as unknown as GapCluster;
    expect(allClusterMembers(legacy)).toEqual(["RAG"]);
  });
});

describe("clusterView — a card's state comes from the server record only", () => {
  it("open: every member a red gap, askable while budget is left, no pill", () => {
    const v = clusterView(cluster());
    expect(v.coverage).toBe("open");
    expect(v.askable).toBe(true);
    expect(v.budgetSpent).toBe(false);
    expect(v.members.map((m) => m.state)).toEqual(["gap", "gap"]);
    expect(v.tone).toBe("red");
    expect(v.pill).toBeNull();
  });

  it("partly covered: each member carries its own ledger fact, still askable", () => {
    const v = clusterView(
      cluster({
        gaps: ["Terraform"],
        outcome: { asked: 1, covered: ["Kubernetes"], declined: [], session_ids: ["s1"] },
        coverage: "partly_covered",
        budget_remaining: 1,
        member_statuses: statuses([["Terraform", "partial"], ["Kubernetes", "covered"]]),
      }),
    );
    expect(v.coverage).toBe("partly_covered");
    expect(v.members).toEqual([
      { term: "Terraform", state: "partial" },
      { term: "Kubernetes", state: "covered" },
    ]);
    expect(v.tone).toBe("yellow");
    expect(v.pill).toBe("partly");
    expect(v.askable).toBe(true);
    expect(v.asked).toBe(1);
  });

  it("budget spent: shown, not askable — distinct from covered", () => {
    const v = clusterView(
      cluster({
        gaps: ["Terraform"],
        outcome: { asked: 2, covered: ["Kubernetes"], declined: [], session_ids: ["s1", "s2"] },
        coverage: "partly_covered",
        budget_remaining: 0,
      }),
    );
    expect(v.askable).toBe(false);
    expect(v.budgetSpent).toBe(true);
  });

  it("covered and declined are finished — never askable, never 'budget spent'", () => {
    for (const coverage of ["covered", "declined"] as const) {
      const v = clusterView(cluster({ coverage, budget_remaining: 1 }));
      expect(v.askable).toBe(false);
      expect(v.budgetSpent).toBe(false);
    }
  });

  it("a declined member is never shown as covered", () => {
    const v = clusterView(
      cluster({
        gaps: [],
        outcome: { asked: 1, covered: ["Kubernetes"], declined: ["Terraform"], session_ids: ["s1"] },
        coverage: "covered",
        member_statuses: statuses([["Kubernetes", "covered"], ["Terraform", "declined"]]),
      }),
    );
    expect(v.members).toEqual([
      { term: "Kubernetes", state: "covered" },
      { term: "Terraform", state: "declined" },
    ]);
  });

  it("a turn's cluster_coverage overrides the row until the page re-reads it; a member it closed reads neutral, not green", () => {
    const v = clusterView(cluster(), {
      cluster_id: "cl-1",
      coverage: "partly_covered",
      open_concepts: ["Terraform"],
      budget_remaining: 1,
    });
    expect(v.coverage).toBe("partly_covered");
    expect(v.members).toEqual([
      { term: "Kubernetes", state: "closed" },
      { term: "Terraform", state: "gap" },
    ]);
    expect(v.tone).toBe("red"); // the closed member never colours the card
    expect(v.askable).toBe(true);
  });

  it("a turn that spends the last question makes the card not askable immediately", () => {
    const v = clusterView(cluster({ budget_remaining: 1 }), {
      cluster_id: "cl-1",
      coverage: "open",
      open_concepts: ["Kubernetes", "Terraform"],
      budget_remaining: 0,
    });
    expect(v.budgetSpent).toBe(true);
  });

  it("legacy row (no coverage, no budget, no member facts): open and askable — the 409 is the fallback; B reads yellow, C red", () => {
    const legacy = {
      id: "cl-x",
      label: "x",
      category: "B",
      gaps: ["Docker"],
      jd_skills: [],
      jd_context: "",
    } as unknown as GapCluster;
    const v = clusterView(legacy);
    expect(v.coverage).toBe("open");
    expect(v.askable).toBe(true);
    expect(v.asked).toBe(0);
    expect(v.members).toEqual([{ term: "Docker", state: "partial" }]);
    expect(v.tone).toBe("yellow");
    expect(clusterView({ ...legacy, category: "C" } as GapCluster).tone).toBe("red");
  });
});

describe("ruling C-1 — the card's colour is its WORST requirement", () => {
  // The founder's own three examples, verbatim in intent.
  it("3 green + 1 yellow → yellow", () => {
    expect(cardTone(["covered", "covered", "covered", "partial"], "partly_covered")).toBe("yellow");
  });
  it("1 green + 1 yellow + 1 red → red", () => {
    expect(cardTone(["covered", "partial", "gap"], "partly_covered")).toBe("red");
  });
  it("5 green → green", () => {
    expect(cardTone(["covered", "covered", "covered", "covered", "covered"], "covered")).toBe("green");
  });
  it("all declined → grey", () => {
    expect(cardTone(["declined", "declined"], "declined")).toBe("grey");
  });
  it("declined members never colour a card that has others", () => {
    expect(cardTone(["covered", "declined"], "covered")).toBe("green");
    expect(cardTone(["partial", "declined"], "partly_covered")).toBe("yellow");
  });

  it("the card view carries the worst member's tone through member_statuses", () => {
    const v = clusterView(
      cluster({
        gaps: ["Helm", "Istio"],
        outcome: { asked: 1, covered: ["Kubernetes"], declined: [], session_ids: ["s1"] },
        coverage: "partly_covered",
        member_statuses: statuses([
          ["Kubernetes", "covered"],
          ["Helm", "partial"],
          ["Istio", "gap"],
        ]),
      }),
    );
    expect(v.tone).toBe("red");
    expect(v.pill).toBe("partly"); // pill TEXT follows coverage, its colour the card
    expect(v.members.map((m) => [m.term, m.state])).toEqual([
      ["Helm", "partial"],
      ["Istio", "gap"],
      ["Kubernetes", "covered"],
    ]);
  });
});

describe("ruling C-1b — a likely match nobody has asked yet", () => {
  const likely = (over: Partial<GapCluster> = {}) =>
    cluster({
      category: "B",
      gaps: ["PostgreSQL", "Redis"],
      coverage: "partly_covered",
      member_statuses: statuses([["PostgreSQL", "partial"], ["Redis", "partial"]]),
      ...over,
    });

  it("asked 0, no red member, at least one yellow → 'likely match'", () => {
    const v = clusterView(likely());
    expect(v.pill).toBe("likely");
    expect(v.tone).toBe("yellow");
  });

  it("after the first answer it follows the normal states", () => {
    const v = clusterView(likely({ outcome: { asked: 1, covered: [], declined: [], session_ids: ["s1"] } }));
    expect(v.pill).toBe("partly");
  });

  it("a red member keeps the normal wording even when nobody asked yet", () => {
    const v = clusterView(likely({ member_statuses: statuses([["PostgreSQL", "partial"], ["Redis", "gap"]]) }));
    expect(v.tone).toBe("red");
    expect(v.pill).toBe("partly");
  });
});

describe("clusterCoverageCounts", () => {
  it("tallies covered and askable clusters from the server record", () => {
    const counts = clusterCoverageCounts([
      cluster({ id: "a" }),
      cluster({ id: "b", coverage: "covered", gaps: [] }),
      cluster({ id: "c", coverage: "partly_covered", budget_remaining: 0 }),
      cluster({ id: "d", coverage: "declined", gaps: [] }),
    ]);
    expect(counts).toEqual({ covered: 1, askable: 1 });
  });
});

describe("isGapRefusalCode", () => {
  it("accepts exactly the two contract codes", () => {
    expect(isGapRefusalCode("gap_budget_spent")).toBe(true);
    expect(isGapRefusalCode("gap_already_covered")).toBe(true);
    expect(isGapRefusalCode("provider_unavailable")).toBe(false);
    expect(isGapRefusalCode(undefined)).toBe(false);
  });
});
