// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
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

function open(group: number) {
  const toggle = screen.getByTestId(`review-group-toggle-${group}`);
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

describe("#703 repeated demands on the surface", () => {
  it("tags the open group-2 row and explains it (DE)", () => {
    render(withIntl(<ReviewSurface {...props()} />, "de"));
    open(2);
    const g2 = screen.getByTestId("review-group-2");
    const tag = within(g2).getByTestId("review-item-signal-tag");
    expect(tag).toHaveTextContent("2× nachgefordert");
    expect(tag.dataset.weight).toBe("open");
    expect(within(g2).getByTestId("review-item-signal-note")).toHaveTextContent(
      "Applire hat in 2 Runden versucht, das unterzubringen — es steht nicht im Anschreiben. Dein Profil deckt es.",
    );
  });

  it("lists late arrivals under Handwerk with the re-read hint (EN)", () => {
    render(withIntl(<ReviewSurface {...props()} />));
    open(4);
    const g4 = screen.getByTestId("review-group-4");
    expect(within(g4).getByText("Arbeitssicherheit")).toBeInTheDocument();
    expect(within(g4).getAllByTestId("review-item-signal-note")[0]).toHaveTextContent(
      "Only made it into the cover letter after 3 attempts.",
    );
    expect(within(g4).getAllByText("Asked for again").length).toBe(2);
  });
});

describe("#702 cross-document section placement", () => {
  it("sits between group 1 and the other findings, and passes the CV edit target", () => {
    render(withIntl(<ReviewSurface {...props({ cvEditHref: "/flow/f/cv?tab=edit" })} />));
    const section = screen.getByTestId("review-xdoc");
    const other = screen.getByTestId("review-other");
    expect(section.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(section).getByTestId("review-xdoc-add-to-cv").getAttribute("href")).toMatch(/^\/flow\/f\/cv\?tab=edit&xdoc=[0-9a-f]{8}$/);
  });
});

describe("#702 RULING R-1 = A — the verdict never contradicts an open card", () => {
  const clean = (): ATSReport => ({ ...ats(), checks: [], keywords: { ...ats()!.keywords, missing_claimable: [] } });

  it("rewords the all-clear while a cross-document item is undecided (DE)", () => {
    render(withIntl(<ReviewSurface {...props({ atsReport: clean() })} />, "de"));
    const verdict = screen.getByTestId("review-verdict");
    expect(verdict).toHaveTextContent("Neben Deinem Lebenslauf gelesen fällt aber eine Stelle auf");
    expect(verdict).not.toHaveTextContent("nicht im Weg");
  });

  it("returns to the plain all-clear once the item is decided", () => {
    const reviewState = {
      walked_at: null,
      decisions: [{ finding_key: "critic:x", label: "x", action: "kept" as const, at: "2026-10-07T12:00:00Z", undo: null }],
    };
    render(withIntl(<ReviewSurface {...props({ atsReport: clean(), reviewState })} />, "de"));
    const verdict = screen.getByTestId("review-verdict");
    expect(verdict).not.toHaveTextContent("Neben Deinem Lebenslauf");
    expect(verdict).toHaveTextContent("Jede Aussage im Dokument ist durch Dein Profil gedeckt.");
  });
});
