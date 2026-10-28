// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
// ADV-HZ5: class-U checks are emitted by the producer with status "not_applicable"
// (terminal_review_outcome.py:1027-1036, ats_audit.py:2271-2277, :2476-2488);
// registry stamps them U (finding_registry.py:134-135,145,153-154).
import { describe, expect, it } from "vitest";
import { buildDeliveryView, type DeliveryInputs } from "../review-groups";
import type { ATSCheck, ATSReport } from "../ats-report";

function ats(checks: ATSCheck[], extra: Partial<NonNullable<ATSReport>> = {}): ATSReport {
  return { checks, keywords: { present: [], missing: [], missing_claimable: [], missing_honest_gap: [], present_unsupported: [], claimable_concepts: [] }, ...extra };
}
function inputs(a: ATSReport): DeliveryInputs {
  return { atsReport: a, truthReport: { version: "2", document_kind: "cv", counts: {}, stated_limit: "", claims: [] } as never, criticReport: null, gapClusters: null, hasClusterProducer: false, openRequirements: null } as DeliveryInputs;
}

describe("ADV-HZ5 — producer-shaped class-U rows", () => {
  it.each([
    ["terminal-review", "terminal-review-not-run", "regenerate"],
    ["terminal-review", "terminal-review-incomplete", "regenerate"],
    ["document-language", "document-language-not-applicable", "recheck"],
    ["narrative-evidence", "narrative-evidence-not-applicable", "recheck"],
  ])("%s / %s (status not_applicable, class U) renders a U row and is counted", (id, key, res) => {
    const v = buildDeliveryView(inputs(ats([
      { id, status: "not_applicable", details: "x", details_key: key, delivery_class: "U", resolver: res } as ATSCheck,
    ], { delivery: { loop_clean: null, removals: [], fixed: [] } } as never)));
    expect(v.unknown.map((u) => u.resolver)).toEqual([res]);
    expect(v.count).toBe(1);
  });
});
