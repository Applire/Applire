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
import {
  budgetUnitIssueLabels,
  countWorkEntryGaps,
  workEntryLabel,
} from "../profile-gaps";

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


// #382 (PO decision 2026-08-08, Option A) — a budget figure with no unit is
// omitted from generated documents, and the omission must be addressed to the
// user AT THE FIELD on the master profile page. The backend owns the rule
// (ADR-066) and states the affected entries as `unit`-thread health issues;
// these two helpers only JOIN that answer to the entry on screen.
describe("workEntryLabel", () => {
  it("matches the backend entry label exactly (`role @ company`)", () => {
    expect(workEntryLabel({ role: "Produktionsleiter", company: "Weberit GmbH" })).toBe(
      "Produktionsleiter @ Weberit GmbH",
    );
  });

  it("strips the dangling separator when one side is missing", () => {
    // Mirrors Python's `f"{role} @ {company}".strip(" @")`.
    expect(workEntryLabel({ role: "", company: "Weberit GmbH" })).toBe("Weberit GmbH");
    expect(workEntryLabel({ role: "Produktionsleiter", company: "" })).toBe(
      "Produktionsleiter",
    );
    expect(workEntryLabel({})).toBe("");
  });

  it("accepts `title` as the legacy alias for `role`, like the backend does", () => {
    expect(workEntryLabel({ title: "Werkstudent", company: "Acme" })).toBe(
      "Werkstudent @ Acme",
    );
  });
});

describe("budgetUnitIssueLabels", () => {
  const UNIT_ISSUE = {
    id: "unit:budget_managed:Produktionsleiter @ Weberit GmbH",
    thread: "unit" as const,
    profile_mismatch_severity: "review" as const,
    summary: "budget_managed: '6000000' states no unit",
    field_ref: "work_experience.budget_managed",
    source_record_ref: "Produktionsleiter @ Weberit GmbH",
  };

  it("collects the labels of entries whose budget needs a unit", () => {
    expect(budgetUnitIssueLabels([UNIT_ISSUE])).toEqual(
      new Set(["Produktionsleiter @ Weberit GmbH"]),
    );
  });

  it("ignores every other thread — the affordance is not a generic issue badge", () => {
    expect(
      budgetUnitIssueLabels([
        {
          ...UNIT_ISSUE,
          id: "conflict:1",
          thread: "conflict" as const,
        },
      ]).size,
    ).toBe(0);
  });

  it("is empty for no health data at all, so the page renders nothing extra", () => {
    expect(budgetUnitIssueLabels(undefined).size).toBe(0);
    expect(budgetUnitIssueLabels([]).size).toBe(0);
  });
});
