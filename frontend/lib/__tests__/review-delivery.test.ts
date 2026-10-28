// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-099 (UAT-2 fix round, WP-C) — the review surface renders a row by the
 * delivery class and resolver READ from the producer's row; nothing here
 * classifies a kind. Each invariant below is a named guard:
 *
 * - F hidden ONLY when `delivery.loop_clean === true` (cl. 4(d)); otherwise it
 *   renders as its fallback (U for a re-run resolver, A otherwise);
 * - S hidden, except while the loop degraded (T-S);
 * - `null` = unclassified → today's fallback, never hidden;
 * - the headline / badge number counts the A and U rows of the rendered view
 *   (cl. 3), the passed line counts passed checks + F rows the loop handled;
 * - group 2, the cross-document items and group-4 advisories never reach the
 *   view; page length is never a row (its one place is the page's card).
 */

import { describe, expect, it } from "vitest";
import {
  buildDeliveryView,
  buildGroup1Rows,
  effectiveClass,
  removeOnly,
  type DeliveryInputs,
} from "../review-groups";
import type { ATSCheck, ATSReport } from "../ats-report";
import type { TruthfulnessReport } from "../truthfulness-display";
import type { ReviewState } from "../api/document-review";

type Verdict = NonNullable<TruthfulnessReport>["claims"][number]["verdict"];

function ats(
  keywords: Partial<NonNullable<ATSReport>["keywords"]> = {},
  checks: ATSCheck[] = [],
  extra: Partial<NonNullable<ATSReport>> = {},
): ATSReport {
  return {
    checks,
    keywords: {
      present: [],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: [],
      claimable_concepts: [],
      ...keywords,
    },
    ...extra,
  };
}

function truth(claims: Array<{ text: string; verdict: Partial<Verdict> }>): TruthfulnessReport {
  return {
    version: "2",
    document_kind: "cv",
    counts: {},
    stated_limit: "",
    claims: claims.map((c) => ({
      claim: { text: c.text, location: "work_history[0].bullets[0]", kind: "achievement" },
      verdict: { verdict: "unbacked", checker: "x", evidence: [], ...c.verdict } as Verdict,
    })),
  };
}

function inputs(over: Partial<DeliveryInputs>): DeliveryInputs {
  return {
    atsReport: ats(),
    truthReport: truth([]),
    // ADV-11: NULL is a critic that did not answer (counted U) — the default ran clean.
    criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 } as unknown as DeliveryInputs["criticReport"],
    gapClusters: [],
    ...over,
  };
}

const SENTENCE = "Automatisierte Unit-, Build- und Integrationstests für 40 Microservices.";

describe("effectiveClass", () => {
  it("hides F only on a clean loop", () => {
    expect(effectiveClass("F", null, true, false)).toBe("hidden");
    expect(effectiveClass("F", null, false, false)).toBe("A");
    expect(effectiveClass("F", null, null, false)).toBe("A");
    expect(effectiveClass("F", null, undefined, false)).toBe("A");
    expect(effectiveClass("F", "recheck", false, false)).toBe("U");
  });
  it("hides S unless the loop degraded (T-S)", () => {
    expect(effectiveClass("S", null, true, false)).toBe("hidden");
    expect(effectiveClass("S", null, true, true)).toBe("unclassified");
  });
  it("never reads a missing class as S", () => {
    expect(effectiveClass(null, null, true, false)).toBe("unclassified");
    expect(effectiveClass(undefined, null, true, false)).toBe("unclassified");
  });
});

describe("buildDeliveryView — group 1", () => {
  it("an A row is a question carrying its resolver; it counts", () => {
    const v = buildDeliveryView(
      inputs({
        truthReport: truth([
          { text: SENTENCE, verdict: { verdict: "misattributed", delivery_class: "A", resolver: "ask_station" } },
        ]),
        atsReport: ats({}, [], { delivery: { loop_clean: true, removals: [] } }),
      }),
    );
    expect(v.questions).toHaveLength(1);
    expect(v.questions[0].item?.resolver).toBe("ask_station");
    expect(v.count).toBe(1);
  });

  it("an F row is hidden on a clean loop and counted in the passed line", () => {
    const report = ats({}, [], { delivery: { loop_clean: true, removals: [] } });
    const v = buildDeliveryView(
      inputs({
        atsReport: report,
        truthReport: truth([{ text: SENTENCE, verdict: { verdict: "unbacked", delivery_class: "F", resolver: "ask_true" } }]),
      }),
    );
    expect(v.questions).toHaveLength(0);
    expect(v.count).toBe(0);
    expect(v.passed).toBe(1);
  });

  it("an F row on an unclean or agent-door loop renders as its A fallback (cl. 4(d)/(e))", () => {
    for (const delivery of [{ loop_clean: false, removals: [] }, null]) {
      const v = buildDeliveryView(
        inputs({
          atsReport: ats({}, [], { delivery }),
          truthReport: truth([{ text: SENTENCE, verdict: { verdict: "unbacked", delivery_class: "F", resolver: "ask_true" } }]),
        }),
      );
      expect(v.questions).toHaveLength(1);
      expect(v.questions[0].item?.resolver).toBe("ask_true");
      expect(v.count).toBe(1);
    }
  });

  it("an unclassified row is never hidden — rendered with today's handles (resolver null)", () => {
    const v = buildDeliveryView(
      inputs({ truthReport: truth([{ text: SENTENCE, verdict: { verdict: "unbacked" } }]) }),
    );
    expect(v.classified).toBe(false);
    expect(v.questions).toHaveLength(1);
    expect(v.questions[0].item?.resolver).toBeNull();
    expect(v.count).toBe(1);
  });

  it("a term row takes its keyword bucket's class — the employer-sentence bucket first", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({
          present_unsupported: ["Logistik-Software", "Kubernetes"],
          present_unsupported_employer_sentence: ["Logistik-Software"],
          delivery: {
            present_unsupported: { delivery_class: "A", resolver: "ask_true" },
            present_unsupported_employer_sentence: { delivery_class: "A", resolver: "remove_only" },
          },
        }),
      }),
    );
    const byLabel = Object.fromEntries(v.questions.map((r) => [r.label, r.item?.resolver]));
    expect(byLabel).toEqual({ "Logistik-Software": "remove_only", Kubernetes: "ask_true" });
    // a CLASSIFIED employer-sentence row is class-A residue and counts
    expect(v.count).toBe(2);
  });

  it("a merged term + Oracle row takes the Oracle verdict's class", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({
          present_unsupported: ["CKA"],
          delivery: { present_unsupported: { delivery_class: "F", resolver: "ask_true" } },
        }),
        truthReport: truth([{ text: "CKA", verdict: { verdict: "inflated", delivery_class: "A", resolver: "ask_achieved" } }]),
      }),
    );
    expect(v.questions.map((r) => r.item?.resolver)).toEqual(["ask_achieved"]);
  });
});

describe("buildDeliveryView — checks", () => {
  const lang: ATSCheck = {
    id: "document-language",
    status: "fail",
    details: "items not written in the document language (de): …",
    details_key: "document-language-mixed",
    details_params: { language: "de", count: 2, examples: "Drove the review; Stakeholder alignment" },
    delivery_class: "A",
    resolver: "translate",
  };

  it("a failed language check is the ONE translate action, counted", () => {
    const v = buildDeliveryView(inputs({ atsReport: ats({}, [lang]) }));
    expect(v.translate.map((i) => i.checkId)).toEqual(["document-language"]);
    expect(v.fallback).toHaveLength(0);
    expect(v.count).toBe(1);
  });

  it("the letter's class-A page-length check is THE card, counted once (B-2)", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [
          { id: "page-length", status: "fail", details_key: "page-length-letter", details_params: { pages: 2, letterPages: 1 }, delivery_class: "A", resolver: "page_length" },
        ]),
      }),
    );
    expect(v.pageLength?.checkId).toBe("page-length");
    expect(v.fallback).toHaveLength(0);
    expect(v.count).toBe(1);
  });

  it("the passed line adds the kinds the loop fixed (delivery.fixed, B-2)", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [{ id: "contact", status: "pass", delivery_class: "S" }], {
          delivery: { loop_clean: true, removals: [], fixed: ["ats:duplicates", "oracle:misattributed"] },
        }),
      }),
    );
    expect(v.passed).toBe(3);
    expect(v.count).toBe(0);
  });

  it("page length is never a row — its one place is the page's card (cl. 7/10)", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [
          { id: "page-length", status: "fail", details: "3 pages", delivery_class: "S" },
          { id: "page-length-2", status: "fail", details: "3 pages" },
          { id: "page-length-kept", status: "pass", details: "kept", details_key: "page-length-kept" },
        ]),
        extraCounted: 1,
      }),
    );
    expect(v.fallback).toHaveLength(0);
    expect(v.unknown).toHaveLength(0);
    expect(v.count).toBe(1); // only the page's own card
  });

  it("a class-U check (terminal review not run) is a U row offering a new version, never the passed line", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [
          { id: "terminal-review", status: "pass", details_key: "terminal-review-not-run", delivery_class: "U", resolver: "regenerate" },
        ]),
      }),
    );
    expect(v.unknown.map((u) => u.resolver)).toEqual(["regenerate"]);
    expect(v.passed).toBe(0);
    expect(v.count).toBe(1);
  });

  it("an S check is hidden and not counted; an unclassified failed check is today's counted row", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [
          { id: "pdf-readback", status: "fail", details: "x", delivery_class: "S" },
          { id: "narrative-evidence", status: "fail", details: "server prose" },
        ]),
      }),
    );
    expect(v.fallback.map((f) => f.item.checkId)).toEqual(["narrative-evidence"]);
    expect(v.count).toBe(1);
  });

  it("an S check renders as today's row while the loop degraded (T-S)", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [{ id: "pdf-readback", status: "fail", details: "x", delivery_class: "S" }]),
        degradation: [{ step: "final" }],
      }),
    );
    expect(v.fallback.map((f) => f.item.checkId)).toEqual(["pdf-readback"]);
  });

  it("the passed line counts passed checks plus F kinds the clean loop fixed", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats(
          {},
          [
            { id: "contact", status: "pass" },
            { id: "dates", status: "pass", details: "fine" },
            { id: "duplicates", status: "fail", details: "x", delivery_class: "F" },
            { id: "x-na", status: "not_applicable" },
          ],
          { delivery: { loop_clean: true, removals: [] } },
        ),
      }),
    );
    expect(v.passed).toBe(3);
    expect(v.count).toBe(0);
  });
});

describe("buildDeliveryView — unknown producers and removals", () => {
  it("a producer that did not run is a U row (Jetzt prüfen), counted", () => {
    const v = buildDeliveryView(inputs({ truthReport: null }));
    expect(v.unknown).toEqual([{ key: "producer-oracle", resolver: "recheck", producer: "oracle" }]);
    expect(v.count).toBe(1);
    const w = buildDeliveryView(inputs({ atsReport: null }));
    expect(w.unknown.map((u) => u.producer)).toEqual(["ats"]);
  });

  it("removals are listed and never counted (cl. 5)", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({}, [], {
          delivery: {
            loop_clean: true,
            removals: [
              { text: "Mentorin für 15 Nachwuchskräfte.", section: "summary", kind: "oracle:unbacked", pinned: false },
            ],
          },
        }),
      }),
    );
    expect(v.removals).toHaveLength(1);
    expect(v.count).toBe(0);
  });

  it("group 2 and critic advisories never reach the view", () => {
    const v = buildDeliveryView(
      inputs({
        atsReport: ats({ missing: ["Terraform"], missing_claimable: ["Terraform"] }),
        criticReport: {
          version: "1",
          verdict: "advisory",
          advisories: [{ kind: "letter_only", message: { de: "x", en: "x" }, severity: "info" }],
        } as unknown as DeliveryInputs["criticReport"],
      }),
    );
    expect(v.count).toBe(0);
    expect(v.questions).toHaveLength(0);
    expect(v.fallback).toHaveLength(0);
  });
});

describe("ADR-099 cl. 6 — 'Stimmt' without a vault change no longer closes a row", () => {
  const item = {
    key: "ats:kubernetes",
    findingKey: "ats:kubernetes",
    label: "Kubernetes",
    kind: "term" as const,
    producers: ["ats" as const],
    severity: "critical" as const,
  };

  it("a `supported` decision on a listed finding leaves it open", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: "ats:kubernetes", label: "Kubernetes", action: "supported", at: "t", undo: null }],
    };
    const rows = buildGroup1Rows([item], state);
    expect(rows.map((r) => r.status)).toEqual(["open"]);
  });

  it("`no_change` on a listed finding = only Entfernen; on a gone finding no label", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: "ats:kubernetes", label: "Kubernetes", action: "no_change", at: "t", undo: null }],
    };
    const [row] = buildGroup1Rows([item], state);
    expect(removeOnly(row)).toBe(true);
    expect(buildGroup1Rows([], state)).toEqual([]);
  });

  it("the translate decision (`send:` key) is never a group-1 row", () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: "send:document-language", label: "", action: "translated", at: "t", undo: null }],
    };
    expect(buildGroup1Rows([], state)).toEqual([]);
  });
});
