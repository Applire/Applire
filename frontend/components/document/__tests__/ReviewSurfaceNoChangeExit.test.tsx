// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * U-13 (Hazelnut build 5, delivery run 4): after an answer that changed
 * nothing, EVERY class-A card that takes an answer keeps an exit that can
 * clear it — never "Entfernen" alone. The kinds are the registry's own
 * (`adr099-answer-kinds.json`, written by
 * `backend/tests/unit/test_u13_every_answer_card_keeps_a_clearing_exit.py`);
 * a kind the registry adds fails here until it has a row builder.
 * The end state is the PERSISTED one (a reload): a `no_change` decision on a
 * still-listed finding.
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { ReviewState } from "@/lib/api/document-review";
import { normQuote } from "@/lib/norm-quote";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import answerKinds from "@/lib/__tests__/fixtures/adr099-answer-kinds.json";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/document-review", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/document-review")>();
  return { ...actual, markWalked: vi.fn().mockResolvedValue({ review_state: null }), listStations: vi.fn().mockResolvedValue([]) };
});

type Verdict = NonNullable<TruthfulnessReport>["claims"][number]["verdict"];

const TERM = "AI engineering";
const SENTENCE = "Combines system architecture and validation experience with current AI engineering.";

function ats(term: boolean): ATSReport {
  return {
    checks: [],
    delivery: { loop_clean: true, removals: [], fixed: [] },
    keywords: {
      present: term ? [TERM] : [],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: term ? [TERM] : [],
      present_unsupported_matches: term ? { [TERM]: [{ form: TERM, stem: false }] } : {},
      claimable_concepts: [],
      delivery: { present_unsupported: { delivery_class: "A", resolver: "ask_true" } },
    },
  } as unknown as ATSReport;
}

function truth(verdict: Partial<Verdict> | null): TruthfulnessReport {
  return {
    version: "2",
    document_kind: "cv",
    counts: {},
    stated_limit: "",
    claims: verdict
      ? [
          {
            claim: { text: SENTENCE, location: "summary", kind: "summary" },
            verdict: { verdict: "unbacked", checker: "numbers", evidence: [], detail: "", ...verdict } as Verdict,
          },
        ]
      : [],
  };
}

/** One row builder per registry kind — the report shape its producer writes. */
function build(kind: string, resolver: string): { props: Partial<ReviewSurfaceProps>; key: string; skill: boolean } {
  if (kind === "keyword:present_unsupported") {
    return { props: { atsReport: ats(true), truthReport: truth(null) }, key: `ats:${normQuote(TERM)}`, skill: true };
  }
  const [, detailKey] = kind.split(":");
  const at = kind.startsWith("oracle@") ? kind.slice("oracle@".length) : null;
  if (!kind.startsWith("oracle")) throw new Error(`no row builder for registry kind ${kind}`);
  const verdict: Partial<Verdict> = {
    verdict: (at ?? (resolver === "ask_achieved" ? "inflated" : "unbacked")) as Verdict["verdict"],
    detail_key: at ? null : detailKey,
    delivery_class: "A",
    resolver,
  } as Partial<Verdict>;
  return {
    props: { atsReport: ats(false), truthReport: truth(verdict) },
    key: `oracle:${normQuote(SENTENCE)}`,
    skill: detailKey === "skillNoEvidence",
  };
}

function noChange(key: string, label: string): ReviewState {
  return { walked_at: null, decisions: [{ finding_key: key, label, action: "no_change", at: "2026-10-29T03:00:00Z", undo: null }] };
}

function props(over: Partial<ReviewSurfaceProps>): ReviewSurfaceProps {
  return {
    documentKind: "cv",
    documentId: "cv-1",
    atsReport: ats(false),
    truthReport: truth(null),
    criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 },
    gapClusters: [],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("U-13 — every answer card keeps a clearing exit after `no_change`", () => {
  it("the producer fixture lists the delivery-run card and the D-10 card", () => {
    const kinds = answerKinds.map((k) => k.kind);
    expect(kinds).toContain("keyword:present_unsupported");
    expect(kinds).toContain("oracle:skillNoEvidence");
  });

  it.each(answerKinds.map((k) => [k.kind, k.resolver]))("%s (%s)", (kind, resolver) => {
    const b = build(kind, resolver);
    const label = kind === "keyword:present_unsupported" ? TERM : SENTENCE;
    render(withIntl(<ReviewSurface {...props({ ...b.props, reviewState: noChange(b.key, label) })} />, "de"));
    const card = screen.getByTestId("review-card");
    expect(card.getAttribute("data-finding-key")).toBe(b.key);
    expect(card.dataset.resolver).toBe(resolver);
    const remove = within(card).queryByTestId("review-answer-remove");
    const answers = within(card).getByTestId("review-answers");
    const exits = Array.from(answers.querySelectorAll("a, button")).filter((el) => el !== remove);
    // never remove alone
    expect(exits.length).toBeGreaterThan(0);
    const why = within(card).queryByTestId("review-card-why")?.textContent ?? "";
    expect(why).not.toContain("Es bleibt nur");
    if (resolver === "ask_true") {
      expect(within(card).getByTestId("review-nochange-profile").getAttribute("href")).toBe("/profile");
      expect(within(card).getByTestId("review-nochange-recheck")).toBeTruthy();
      expect(remove).toBeTruthy();
      expect(why).toContain(b.skill ? "nicht in diesen Worten" : "an der passenden Stelle");
    } else {
      expect(within(card).getByTestId("review-answer-as-goal")).toBeTruthy();
    }
  });
});
