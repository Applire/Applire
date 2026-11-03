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

/**
 * ADV-10 / ADV-11 — the U rows by PRODUCER shape. `fixtures/adr099-u-states.json`
 * is generated from the real producers by `tests/unit/test_adr099_u_states.py`
 * (terminal-review outcome → check → registry stamp; a failed class-F check on
 * an unclean loop); no row here is hand-written.
 */
import { describe, expect, it } from "vitest";
import { buildDeliveryView, isUnreviewedAcknowledged, unknownState, type DeliveryInputs, type UnknownState } from "../review-groups";
import type { ATSCheck, ATSReport } from "../ats-report";
import produced from "./fixtures/adr099-u-states.json";

const CLEAN_CRITIC = { ran: true, mount: "cv", advisories: [], dropped_citations: 0 };
const CLEAN_TRUTH = { version: "2", document_kind: "cv", counts: {}, stated_limit: "", claims: [] };

function inputs(checks: ATSCheck[], loopClean: boolean | null, critic: unknown = CLEAN_CRITIC): DeliveryInputs {
  const atsReport = {
    checks,
    keywords: { present: [], missing: [], missing_claimable: [], missing_honest_gap: [], present_unsupported: [], claimable_concepts: [] },
    delivery: { loop_clean: loopClean, removals: [], fixed: [] },
  } as unknown as ATSReport;
  return { atsReport, truthReport: CLEAN_TRUTH as never, criticReport: critic as never, gapClusters: null, hasClusterProducer: false, openRequirements: null } as DeliveryInputs;
}

const EXPECTED: Record<string, UnknownState> = {
  "terminal-review-not-run": "notRun",
  "terminal-review-incomplete": "unfinished",
  "terminal-review-unnamed": "unfinished",
  "terminal-review-unverified": "earlierDraft",
  "terminal-review-unverified-clean": "earlierDraft",
  "duplicate-bullets": "foundNotFixed",
};

describe("ADV-10 — a U row says what the producer wrote", () => {
  it("the fixture covers every U state the producers emit", () => {
    expect(new Set((produced as ATSCheck[]).map((r) => r.details_key))).toEqual(new Set(Object.keys(EXPECTED)));
  });

  it.each((produced as ATSCheck[]).map((r) => [r.details_key, r] as const))(
    "%s → its own state, one counted row, the stamped resolver",
    (key, row) => {
      const v = buildDeliveryView(inputs([row as ATSCheck], row.id === "duplicate-bullets" ? false : true));
      expect(v.unknown).toHaveLength(1);
      expect(v.unknown[0].resolver).toBe(row.resolver);
      expect(unknownState(v.unknown[0])).toBe(EXPECTED[key as string]);
      expect(v.count).toBe(1);
    },
  );
});

describe("ADV-11 — a critic that raised (report NULL) is the same Nicht geprüft as one that never answered", () => {
  it.each([
    ["NULL (the in-loop critic raised)", null],
    ["ran:false", { ran: false, mount: "cv", advisories: [], dropped_citations: 0 }],
    ["judgement_error", { ran: true, reason: "judgement_error", mount: "cv", advisories: [], dropped_citations: 0 }],
  ])("%s → one counted critic U row with a new version", (_label, critic) => {
    const v = buildDeliveryView(inputs([], true, critic));
    expect(v.unknown.map((u) => [u.producer, u.resolver])).toEqual([["critic", "regenerate"]]);
    expect(unknownState(v.unknown[0])).toBe("notRun");
    expect(v.count).toBe(1);
  });

  it("a critic that ran clean adds nothing", () => {
    const v = buildDeliveryView(inputs([], true));
    expect(v.unknown).toEqual([]);
    expect(v.count).toBe(0);
  });
});

describe("R-2 — isUnreviewedAcknowledged: acknowledged only while the decision's basis equals the check's CURRENT delta hash", () => {
  const check = (hash: string | null | undefined) =>
    ({ id: "terminal-review", status: "warn", details_key: "terminal-review-unverified", unreviewed_changes_hash: hash }) as unknown as ATSCheck;
  const state = (basis: string | null | undefined, action = "kept", key = "send:terminal-review") =>
    ({ walked_at: null, decisions: [{ finding_key: key, label: "x", action, at: "t", undo: null, ...(basis === undefined ? {} : { basis }) }] }) as never;

  it("same hash → acknowledged", () => expect(isUnreviewedAcknowledged(check("bbb"), state("bbb"))).toBe(true));
  it("the delta changed (basis aaa, check bbb) → NOT acknowledged", () => expect(isUnreviewedAcknowledged(check("bbb"), state("aaa"))).toBe(false));
  it("legacy: no hash and basis null → acknowledged", () => expect(isUnreviewedAcknowledged(check(null), state(null))).toBe(true));
  it("legacy: absent hash and absent basis → acknowledged", () => expect(isUnreviewedAcknowledged(check(undefined), state(undefined))).toBe(true));
  it("a hashed delta and a legacy (null) decision → not acknowledged", () => expect(isUnreviewedAcknowledged(check("bbb"), state(null))).toBe(false));
  it("other action or other key → not acknowledged", () => {
    expect(isUnreviewedAcknowledged(check("bbb"), state("bbb", "added"))).toBe(false);
    expect(isUnreviewedAcknowledged(check("bbb"), state("bbb", "kept", "critic:x"))).toBe(false);
    expect(isUnreviewedAcknowledged(check("bbb"), null)).toBe(false);
  });

  it("in the view: a void decision leaves the row counted; a matching one removes it from the count but keeps it listed", () => {
    const row = { ...(produced as ATSCheck[]).find((r) => r.details_key === "terminal-review-unverified")!, unreviewed_changes: [{ section_id: "summary", before: "a.", after: "b." }], unreviewed_changes_hash: "bbb" } as ATSCheck;
    const base = inputs([row], true);
    const stale = buildDeliveryView({ ...base, reviewState: state("aaa") });
    expect(stale.count).toBe(1);
    expect(stale.unknown[0].acknowledged).toBe(false);
    const ack = buildDeliveryView({ ...base, reviewState: state("bbb") });
    expect(ack.count).toBe(0);
    expect(ack.unknown).toHaveLength(1);
    expect(ack.unknown[0].acknowledged).toBe(true);
    expect(ack.unknown[0].changes).toHaveLength(1);
  });
});
