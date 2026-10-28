// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * (Superseded in part by ADR-099 cl. 8 — see the describes below.)
 * #703 (RULING R-3 = A) and #702 on the review surface itself: the repeated-
 * demand tag on the open group-2 row, the late-arrival row under Handwerk, and
 * the cross-document section mounted between group 1 and the other findings.
 * The signal payloads are the backend's shape for the 2026-09-13 replay.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSCheck, ATSReport, CheckSignal } from "@/lib/ats-report";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const sig = (term: string, weight: "open" | "landed", rounds: number): CheckSignal => ({
  kind: "repeated_demand", term, rounds, total_rounds: 8, in_document: weight === "landed", weight,
});

const TERMINAL: ATSCheck = {
  id: "terminal-review",
  status: "fail",
  details: "Asked for again and again while writing: …",
  driver: { repeated_demands: 3, open: 1 },
  signals: [sig("Maschinendatenerfassung", "open", 2), sig("Arbeitssicherheit", "landed", 3), sig("Arbeitsvorbereitung", "landed", 2)],
};

function ats(): ATSReport {
  return {
    checks: [TERMINAL],
    keywords: {
      present: [], missing: [], missing_claimable: ["OEE", "Maschinendatenerfassung"],
      missing_honest_gap: [], present_unsupported: [], claimable_concepts: [],
    },
  };
}

const CRITIC: OutcomeCriticReport = {
  ran: true, mount: "letter", dropped_citations: 0, advisories: [],
  cross_document: [{
    key: "critic:x", letter_state: "Aus Kosmetik-Verpackungen seit 2021.", concepts: ["Kosmetik-Verpackungen"],
    kinds: ["letter_only"], weight: "high", figures: ["2021"],
  }],
};

function props(over: Partial<ReviewSurfaceProps> = {}): ReviewSurfaceProps {
  return {
    documentKind: "cover-letter", documentId: "cl-1", atsReport: ats(),
    truthReport: { version: "1", document_kind: "cover_letter", claims: [], counts: {}, stated_limit: "" },
    criticReport: CRITIC, gapClusters: [], hasClusterProducer: false, ...over,
  };
}

// ADR-099 cl. 8: the cross-document section, the repeated-demand tags on
// group 2 and the late-arrival rows under "Handwerk" left the surface (S).
// What survives from these suites is cl. 2's U: a critic that did not answer
// is "Nicht geprüft", counted, and never the plain all-clear (T-U).
describe("ADR-099 cl. 8 — signals and the cross-read are not on the surface", () => {
  it("no group-2 tag, no Handwerk row, no cross-document section", () => {
    render(withIntl(<ReviewSurface {...props({ cvEditHref: "/flow/f/cv?tab=edit" })} />, "de"));
    expect(screen.queryByTestId("review-group-2")).toBeNull();
    expect(screen.queryByTestId("review-group-4")).toBeNull();
    expect(screen.queryByTestId("review-item-signal-tag")).toBeNull();
    expect(screen.queryByTestId("review-xdoc")).toBeNull();
    expect(screen.queryByTestId("review-other")).toBeNull();
    const text = screen.getByTestId("review-surface").textContent ?? "";
    expect(text).not.toContain("Kosmetik-Verpackungen");
    expect(text).not.toContain("nachgefordert");
  });

  it("an undecided cross-document item neither counts nor rewords the clear line (DE)", () => {
    const clean: ATSReport = { ...ats(), checks: [], keywords: { ...ats()!.keywords, missing_claimable: [] } };
    render(withIntl(<ReviewSurface {...props({ atsReport: clean })} />, "de"));
    const verdict = screen.getByTestId("review-verdict");
    expect(verdict.getAttribute("data-count")).toBe("0");
    expect(verdict).toHaveTextContent("Nichts braucht Dich mehr – das Dokument ist versandbereit.");
    expect(verdict).not.toHaveTextContent("Neben Deinem Lebenslauf");
  });
});

describe("ADR-099 T-U — a critic that did not answer is unknown, never an all-clear", () => {
  const clean = (): ATSReport => ({ ...ats(), checks: [], keywords: { ...ats()!.keywords, missing_claimable: [] } });
  const errored: OutcomeCriticReport = {
    ran: true, reason: "judgement_error", mount: "letter", dropped_citations: 0, advisories: [], cross_document: [],
  };
  const notRun: OutcomeCriticReport = { ran: false, mount: "cv", dropped_citations: 0, advisories: [] };

  for (const [name, report, kind] of [
    ["judgement_error, letter", errored, "cover-letter"],
    ["ran:false, CV", notRun, "cv"],
  ] as const) {
    it(`counts one "Nicht geprüft" row with the new-version action (${name}, DE)`, () => {
      const onRegenerate = vi.fn();
      render(withIntl(<ReviewSurface {...props({ documentKind: kind, atsReport: clean(), criticReport: report, onRegenerate })} />, "de"));
      const verdict = screen.getByTestId("review-verdict");
      expect(verdict.getAttribute("data-count")).toBe("1");
      expect(verdict).not.toHaveTextContent("versandbereit");
      const box = screen.getByTestId("review-unknown");
      expect(box).toHaveTextContent("Nicht geprüft: Kohärenz-Hinweis");
      expect(box).toHaveTextContent("Applire prüft das nur beim Schreiben");
      expect(box.dataset.resolver).toBe("regenerate");
      fireEvent.click(within(box).getByTestId("review-unknown-regenerate"));
      expect(onRegenerate).toHaveBeenCalledTimes(1);
    });
  }

  it("a critic that ran and found nothing keeps the plain all-clear", () => {
    render(withIntl(<ReviewSurface {...props({ atsReport: clean(), criticReport: { ...errored, reason: null } })} />, "de"));
    expect(screen.queryByTestId("review-unknown")).toBeNull();
    expect(screen.getByTestId("review-verdict")).toHaveTextContent(/^Nichts braucht Dich mehr – das Dokument ist versandbereit\.$/);
  });

  it("EN: the unknown row names the producer", () => {
    render(withIntl(<ReviewSurface {...props({ atsReport: clean(), criticReport: notRun, documentKind: "cv" })} />, "en"));
    expect(screen.getByTestId("review-unknown")).toHaveTextContent("Coherence advisory");
  });
});
