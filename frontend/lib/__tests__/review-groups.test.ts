// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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
import { buildGroup1Rows, buildReviewGroups, verdictState, type ReviewInputs } from "../review-groups";
import type { ReviewState } from "../api/document-review";
import type { ATSReport } from "../ats-report";
import type { TruthfulnessReport } from "../truthfulness-display";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

function ats(partial: Partial<NonNullable<ATSReport>["keywords"]> = {}, checks: NonNullable<ATSReport>["checks"] = []): ATSReport {
  return {
    checks,
    keywords: {
      present: [],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: [],
      claimable_concepts: [],
      ...partial,
    },
  };
}

function truth(claims: NonNullable<TruthfulnessReport>["claims"]): TruthfulnessReport {
  return {
    version: "1",
    document_kind: "cv",
    claims,
    counts: {},
    stated_limit: "",
  };
}

function claim(text: string, verdict: "unbacked" | "inflated" | "grounded", kind = "skill") {
  return {
    claim: { text, location: "work-0", kind },
    verdict: { verdict, checker: "literal", evidence: [], detail: null },
  } as NonNullable<TruthfulnessReport>["claims"][number];
}

const CRITIC_RAN: OutcomeCriticReport = {
  ran: true,
  mount: "cv",
  advisories: [],
  dropped_citations: 0,
};

function inputs(over: Partial<ReviewInputs> = {}): ReviewInputs {
  return {
    atsReport: ats(),
    truthReport: truth([]),
    criticReport: CRITIC_RAN,
    gapClusters: [],
    ...over,
  };
}

const byId = (groups: ReturnType<typeof buildReviewGroups>, id: 1 | 2 | 3 | 4) =>
  groups.find((g) => g.id === id)!;

describe("buildReviewGroups — ADR-081 clause 2 ordering", () => {
  it("returns exactly four groups in the fixed severity-and-action order", () => {
    const groups = buildReviewGroups(inputs());
    expect(groups.map((g) => g.id)).toEqual([1, 2, 3, 4]);
  });

  it("group 1 unions present_unsupported with the Oracle's flagged claims", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ present_unsupported: ["Kubernetes"] }),
        truthReport: truth([claim("Teamleitung von 12 Personen", "inflated", "achievement")]),
      }),
    );
    const g1 = byId(groups, 1);
    expect(g1.items.map((i) => i.label)).toEqual(["Kubernetes", "Teamleitung von 12 Personen"]);
    expect(g1.items[0].producers).toEqual(["ats"]);
    expect(g1.items[1].producers).toEqual(["oracle"]);
  });

  it("group 2 carries the missing_claimable residue and never a document-writing action", () => {
    const groups = buildReviewGroups(inputs({ atsReport: ats({ missing_claimable: ["SAP PP"] }) }));
    const g2 = byId(groups, 2);
    expect(g2.items).toHaveLength(1);
    expect(g2.items[0].label).toBe("SAP PP");
    expect(g2.items[0].severity).toBe("warning");
  });

  it("group 3 keeps the two granularities distinct and labels each item by origin", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ missing_honest_gap: ["Kubernetes"] }),
        gapClusters: [{ id: "g1", label: "Cloud-Betrieb", kind: "honest" }],
      }),
    );
    const g3 = byId(groups, 3);
    expect(g3.items.map((i) => [i.label, i.kind])).toEqual([
      ["Kubernetes", "term"],
      ["Cloud-Betrieb", "cluster"],
    ]);
  });

  it("routes a CLAIMABLE gap cluster to group 2, not group 3 (the producer's own kind field)", () => {
    const groups = buildReviewGroups(
      inputs({ gapClusters: [{ id: "g9", label: "Produktionsplanung", kind: "claimable" }] }),
    );
    expect(byId(groups, 2).items.map((i) => i.label)).toEqual(["Produktionsplanung"]);
    expect(byId(groups, 3).items).toHaveLength(0);
  });

  it("group 4 counts failing/advisory/not-applicable checks and advisories, never plain passes", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({}, [
          { id: "contact-0", status: "pass" },
          { id: "headings-0", status: "pass" },
          { id: "page-length-0", status: "fail", details: "3 pages" },
          { id: "page-length-1", status: "pass", details: "beyond the norm by choice" },
          { id: "page-length-2", status: "not_applicable" },
        ]),
        criticReport: {
          ran: true,
          mount: "cv",
          dropped_citations: 0,
          advisories: [
            { concept: "Kostensenkung", kind: "letter_only", changed: false, message: { de: "x", en: "x" } },
          ],
        },
      }),
    );
    const g4 = byId(groups, 4);
    expect(g4.items).toHaveLength(4);
    expect(g4.passedChecks).toBe(2);
    expect(g4.items.filter((i) => i.severity === "critical")).toHaveLength(1);
  });

  it("deduplicates gap clusters by id (the aggregate count must not repeat a cluster)", () => {
    const groups = buildReviewGroups(
      inputs({
        gapClusters: [
          { id: "g1", label: "Cloud", kind: "honest" },
          { id: "g1", label: "Cloud", kind: "honest" },
        ],
      }),
    );
    expect(byId(groups, 3).items).toHaveLength(1);
  });
});

describe("SF-REVIEW.3 — the group-1 carve-out, pinned in BOTH directions", () => {
  it("collapses a genuine overlap to ONE row citing both producers", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ present_unsupported: ["Lean-Management"] }),
        truthReport: truth([claim("Lean–Management", "unbacked")]),
      }),
    );
    const g1 = byId(groups, 1);
    expect(g1.items).toHaveLength(1);
    expect(g1.items[0].producers).toEqual(["ats", "oracle"]);
    expect(g1.items[0].label).toBe("Lean-Management");
  });

  it("does NOT collapse two findings that merely resemble each other", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ present_unsupported: ["SAP PP"] }),
        truthReport: truth([claim("SAP PP/DS", "unbacked")]),
      }),
    );
    const g1 = byId(groups, 1);
    expect(g1.items).toHaveLength(2);
    expect(g1.items.every((i) => i.producers.length === 1)).toBe(true);
  });

  it("two terms that fold to one finding key are ONE finding (ADR-090 cl. 6), merged with at most one claim", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ present_unsupported: ["Scrum", "scrum"] }),
        truthReport: truth([claim("Scrum", "unbacked")]),
      }),
    );
    const g1 = byId(groups, 1);
    expect(g1.items).toHaveLength(1);
    expect(g1.items[0].producers).toEqual(["ats", "oracle"]);
    expect(g1.items[0].findingKey).toBe("ats:scrum");
  });

  it("excludes the #249 'related' third state from group 1, exactly as the panel excludes it", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ claimable_concepts: ["kubernetes"] }),
        truthReport: truth([claim("Kubernetes", "unbacked", "skill")]),
      }),
    );
    expect(byId(groups, 1).items).toHaveLength(0);
  });

  it("is the ONLY cross-producer suppression — group 3 does not collapse a term against a cluster of the same name", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({ missing_honest_gap: ["Kubernetes"] }),
        gapClusters: [{ id: "g1", label: "Kubernetes", kind: "honest" }],
      }),
    );
    expect(byId(groups, 3).items).toHaveLength(2);
  });
});

describe("SF-REVIEW.4 / ADR-081 clause 9 — a producer that did not run is UNKNOWN, never zero", () => {
  it("marks the Oracle unknown on group 1 when no truthfulness report was persisted", () => {
    const g1 = byId(buildReviewGroups(inputs({ truthReport: null })), 1);
    expect(g1.unknownProducers).toEqual(["oracle"]);
    expect(g1.unknown).toBe(false); // the ATS half still ran
  });

  it("marks group 1 wholly unknown when NEITHER producer ran", () => {
    const g1 = byId(buildReviewGroups(inputs({ truthReport: null, atsReport: null })), 1);
    expect(g1.unknown).toBe(true);
  });

  it("marks the outcome critic unknown on group 4 when it did not run (ADR-060 ran:false)", () => {
    const g4 = byId(
      buildReviewGroups(inputs({ criticReport: { ran: false, advisories: [], dropped_citations: 0 } })),
      4,
    );
    expect(g4.unknownProducers).toEqual(["critic"]);
  });

  it("treats an absent critic report the same as ran:false", () => {
    const g4 = byId(buildReviewGroups(inputs({ criticReport: null })), 4);
    expect(g4.unknownProducers).toEqual(["critic"]);
  });

  it("marks the cluster producer unknown on group 3 when the clusters were never loaded", () => {
    const g3 = byId(buildReviewGroups(inputs({ gapClusters: null })), 3);
    expect(g3.unknownProducers).toEqual(["clusters"]);
  });

  it("distinguishes 'loaded, none found' (empty array) from 'never ran' (null)", () => {
    expect(byId(buildReviewGroups(inputs({ gapClusters: [] })), 3).unknownProducers).toEqual([]);
    expect(byId(buildReviewGroups(inputs({ gapClusters: null })), 3).unknownProducers).toEqual(["clusters"]);
  });

  it("marks the ATS report unknown on every group when no report was persisted", () => {
    const groups = buildReviewGroups(inputs({ atsReport: null }));
    expect(groups.every((g) => g.unknownProducers.includes("ats"))).toBe(true);
  });
});

describe("verdictState — ADR-081 clause 4 and JF-F-K.1", () => {
  it("reports UNKNOWN rather than a clean verdict when group 1's producers did not run", () => {
    const groups = buildReviewGroups(inputs({ atsReport: null, truthReport: null }));
    expect(verdictState(groups, 0)).toEqual({ kind: "unknown" });
  });

  it("counts the rows the caller actually rendered, not a recomputed population", () => {
    const groups = buildReviewGroups(
      inputs({ atsReport: ats({ present_unsupported: ["A", "B", "C"] }) }),
    );
    // The caller renders the array it built; the sentence takes its length.
    expect(verdictState(groups, byId(groups, 1).items.length)).toEqual({ kind: "findings", count: 3 });
  });

  it("qualifies the all-clear when group 1 is clean but groups 2-4 are not (JF-F-K.1)", () => {
    const groups = buildReviewGroups(
      inputs({ atsReport: ats({ missing_honest_gap: ["Kubernetes", "Terraform"] }) }),
    );
    expect(verdictState(groups, 0)).toEqual({ kind: "clear_with_others", others: 2 });
  });

  it("qualifies the all-clear when another group is UNKNOWN, even with nothing else found", () => {
    const groups = buildReviewGroups(inputs({ criticReport: null }));
    expect(verdictState(groups, 0)).toEqual({ kind: "clear_with_others", others: 0 });
  });

  it("renders a plain all-clear only when every group ran and every group is empty", () => {
    expect(verdictState(buildReviewGroups(inputs()), 0)).toEqual({ kind: "clear" });
  });

  it("never lets groups 2-4 enter the headline's number", () => {
    const groups = buildReviewGroups(
      inputs({
        atsReport: ats({
          present_unsupported: ["Kubernetes"],
          missing_claimable: ["SAP PP", "Six Sigma"],
          missing_honest_gap: ["Terraform"],
        }),
      }),
    );
    expect(verdictState(groups, byId(groups, 1).items.length)).toEqual({ kind: "findings", count: 1 });
  });
});

describe("ADR-090 cl. 6 — finding_key without a list index", () => {
  it("keys a term row as ats:<normQuote> and a claim row as oracle:<normQuote>", () => {
    const g1 = byId(
      buildReviewGroups(
        inputs({
          atsReport: ats({ present_unsupported: ["IT Data & AI Governance"] }),
          truthReport: truth([claim("Led the AI\u2011governance board", "unbacked", "achievement")]),
        }),
      ),
      1,
    );
    expect(g1.items.map((i) => i.findingKey)).toEqual([
      "ats:it data & ai governance",
      "oracle:led the ai governance board",
    ]);
  });

  it("keeps a finding's key when an EARLIER finding clears (the index-bearing key did not)", () => {
    const before = byId(buildReviewGroups(inputs({ atsReport: ats({ present_unsupported: ["Snowflake", "Collibra"] }) })), 1);
    const after = byId(buildReviewGroups(inputs({ atsReport: ats({ present_unsupported: ["Collibra"] }) })), 1);
    expect(after.items[0].findingKey).toBe(before.items[1].findingKey);
    expect(after.items[0].key).toBe(before.items[1].key);
  });
});

describe("ADR-090 cl. 2 / Contract 3 — locate targets", () => {
  it("carries the matched forms (with the stem flag) as the targets", () => {
    const g1 = byId(
      buildReviewGroups(
        inputs({
          atsReport: ats({
            present_unsupported: ["IT Data & AI Governance"],
            present_unsupported_matches: {
              "IT Data & AI Governance": [
                { form: "AI governance", stem: false },
                { form: "Governing", stem: true },
              ],
            },
          }),
        }),
      ),
      1,
    );
    expect(g1.items[0].targets).toEqual([
      { form: "AI governance", stem: false },
      { form: "Governing", stem: true },
    ]);
  });

  it("an absent key means NO DATA (null targets → could not be marked), never a literal search", () => {
    const g1 = byId(buildReviewGroups(inputs({ atsReport: ats({ present_unsupported: ["Snowflake"] }) })), 1);
    expect(g1.items[0].targets).toBeNull();
  });

  it("locates an Oracle claim by its text", () => {
    const g1 = byId(buildReviewGroups(inputs({ truthReport: truth([claim("Kafka", "unbacked")]) })), 1);
    expect(g1.items[0].targets).toEqual([{ form: "Kafka" }]);
  });
});

describe("buildGroup1Rows — counts from the live report, decisions only label (ADR-090 cl. 6, SF-REVIEW.9)", () => {
  const items = byId(
    buildReviewGroups(inputs({ atsReport: ats({ present_unsupported: ["Snowflake", "Collibra"] }) })),
    1,
  ).items;

  function state(...decisions: Array<[string, "added" | "taken_out" | "edited", string]>): ReviewState {
    return {
      walked_at: null,
      decisions: decisions.map(([finding_key, action, at]) => ({
        finding_key,
        label: finding_key.split(":")[1],
        action,
        at,
        undo: null,
      })),
    };
  }

  it("a finding the report still lists is OPEN, whatever the state says", () => {
    const rows = buildGroup1Rows(items, state(["ats:snowflake", "taken_out", "2026-09-23T10:00:00Z"]));
    const snow = rows.find((r) => r.findingKey === "ats:snowflake")!;
    expect(snow.status).toBe("open");
    expect(snow.decision?.action).toBe("taken_out");
    expect(rows.filter((r) => r.status === "open")).toHaveLength(2);
  });

  it("a decision whose finding is gone is a decided row, before the open ones", () => {
    const rows = buildGroup1Rows(items, state(["ats:data mesh", "taken_out", "2026-09-23T10:00:00Z"]));
    expect(rows.map((r) => [r.label, r.status])).toEqual([
      ["data mesh", "taken_out"],
      ["Snowflake", "open"],
      ["Collibra", "open"],
    ]);
  });

  it("the latest decision per finding wins, and a merged row matches under either producer", () => {
    const rows = buildGroup1Rows(
      [],
      state(["oracle:kafka", "added", "2026-09-23T10:00:00Z"], ["ats:kafka", "edited", "2026-09-23T11:00:00Z"]),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("edited");
  });

  it("null state = every listed finding open, nothing decided", () => {
    expect(buildGroup1Rows(items, null).map((r) => r.status)).toEqual(["open", "open"]);
  });
});
