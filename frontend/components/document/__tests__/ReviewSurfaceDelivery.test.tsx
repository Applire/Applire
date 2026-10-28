// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-099 (UAT-2 fix round, WP-C) on the review SURFACE: the card asks the
 * question its resolver names, with exactly that resolver's answers (cl. 6);
 * the headline counts A + U + the page's counted cards (cl. 3); F is hidden
 * only on a clean loop, S never renders (cl. 2/4); the removal line is
 * collapsed unless a pinned fact was removed (cl. 5); no server prose (cl. 12).
 * The rows below carry the class and resolver the producer stamps — the
 * surface never classifies a kind itself.
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport, DeliveryState } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const api = vi.hoisted(() => ({
  addEvidence: vi.fn(),
  confirmSupported: vi.fn(),
  takeOut: vi.fn(),
  undoDecision: vi.fn(),
  markWalked: vi.fn(),
  listStations: vi.fn(),
  moveToStation: vi.fn(),
  rewriteAsGoal: vi.fn(),
  translateSpans: vi.fn(),
  recheckDocument: vi.fn(),
  removalEvidence: vi.fn(),
  shortenLetter: vi.fn(),
  keepLetterLength: vi.fn(),
}));
vi.mock("@/lib/api/document-review", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/document-review")>();
  return { ...actual, ...api };
});

type Verdict = NonNullable<TruthfulnessReport>["claims"][number]["verdict"];

const SENTENCE = "Automatisierte Integrationstests für 40 Microservices eingeführt.";

function truth(verdict: Partial<Verdict>, text = SENTENCE): TruthfulnessReport {
  return {
    version: "2",
    document_kind: "cv",
    counts: {},
    stated_limit: "",
    claims: [
      {
        claim: { text, location: "work_history[0].bullets[0]", kind: "achievement" },
        verdict: {
          verdict: "unbacked",
          checker: "numbers",
          evidence: [],
          detail: "No vault evidence for figure(s): 40.",
          ...verdict,
        } as Verdict,
      },
    ],
  };
}

function ats(delivery: DeliveryState | undefined = { loop_clean: true, removals: [], fixed: [] }, checks: NonNullable<ATSReport>["checks"] = []): ATSReport {
  return {
    checks,
    keywords: {
      present: [],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
      present_unsupported: [],
      claimable_concepts: [],
    },
    delivery,
  } as ATSReport;
}

function props(over: Partial<ReviewSurfaceProps> = {}): ReviewSurfaceProps {
  return {
    documentKind: "cv",
    documentId: "cv-1",
    atsReport: ats(),
    truthReport: truth({ delivery_class: "A", resolver: "ask_true" } as Partial<Verdict>),
    criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 },
    gapClusters: [],
    ...over,
  };
}

const count = () => screen.getByTestId("review-verdict").getAttribute("data-count");

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.markWalked.mockResolvedValue({ review_state: null });
  api.listStations.mockResolvedValue([]);
});

describe("ADR-099 cl. 6 — one question per resolver, with only its answers", () => {
  it("ask_true: „Stimmt das?“ — yes-with-where and no-remove, nothing else (DE)", () => {
    render(withIntl(<ReviewSurface {...props()} />, "de"));
    const card = screen.getByTestId("review-card");
    expect(card.dataset.resolver).toBe("ask_true");
    expect(within(card).getByTestId("review-card-question").textContent).toBe("Stimmt das?");
    expect(within(card).getByTestId("review-card-claim").textContent).toBe(`„${SENTENCE}“`);
    expect(within(card).getByTestId("review-answer-yes").textContent).toContain("Ja, und zwar bei …");
    expect(within(card).getByTestId("review-answer-remove").textContent).toContain("Nein, entfernen");
    expect(within(card).queryByTestId("review-action-add")).toBeNull();
    expect(within(card).queryByTestId("review-action-takeout")).toBeNull();
    expect(within(card).queryByTestId("review-action-edit")).toBeNull();
    expect(count()).toBe("1");
  });

  it("ask_true: an answer that did not change the profile leaves only „Entfernen“", async () => {
    api.addEvidence.mockResolvedValue({
      testimony: { status: "no_change", changes: [] },
      remove_only: true,
      still_listed: true,
      report: null,
      truthfulness: null,
      review_state: null,
    });
    render(withIntl(<ReviewSurface {...props()} />, "de"));
    fireEvent.click(screen.getByTestId("review-answer-yes"));
    fireEvent.change(screen.getByTestId("review-add-text"), { target: { value: "Bei Nordwind, 2021." } });
    fireEvent.click(screen.getByTestId("review-add-submit"));
    await waitFor(() => expect(screen.queryByTestId("review-answer-yes")).toBeNull());
    const card = screen.getByTestId("review-card");
    expect(within(card).getByTestId("review-card-why").textContent).toContain("Es bleibt nur: entfernen.");
    expect(within(card).getByTestId("review-answer-remove").textContent).toContain("Entfernen");
    expect(api.addEvidence).toHaveBeenCalledWith("cv", "cv-1", expect.any(String), "Bei Nordwind, 2021.");
  });

  it("ask_station: „Wohin gehört das?“ lists the owning stations as answers, then remove", async () => {
    api.listStations.mockResolvedValue([
      { station_id: "exp-2", title: "Teamleiterin Plattform", organization: "Nordwind Logistik GmbH" },
    ]);
    api.moveToStation.mockResolvedValue({ changes: [], still_listed: false, report: null, truthfulness: null, review_state: null });
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "A", resolver: "ask_station" } as Partial<Verdict>) })} />, "de"));
    expect(screen.getByTestId("review-card-question").textContent).toBe("Wohin gehört das?");
    const station = await screen.findByTestId("review-answer-station");
    expect(station.textContent).toContain("Teamleiterin Plattform · Nordwind Logistik GmbH");
    expect(screen.getByTestId("review-answer-remove")).toBeTruthy();
    fireEvent.click(station);
    await waitFor(() => expect(api.moveToStation).toHaveBeenCalledWith("cv", "cv-1", expect.any(String), "exp-2"));
  });

  it("ask_achieved: „Ist das erreicht?“ — yes, or rewrite as a goal", async () => {
    api.rewriteAsGoal.mockResolvedValue({ changes: [], still_listed: false, report: null, truthfulness: null, review_state: null });
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "A", resolver: "ask_achieved" } as Partial<Verdict>) })} />, "de"));
    expect(screen.getByTestId("review-card-question").textContent).toBe("Ist das erreicht?");
    expect(screen.getByTestId("review-answer-yes").textContent).toContain("Ja, erreicht");
    fireEvent.click(screen.getByTestId("review-answer-as-goal"));
    await waitFor(() => expect(api.rewriteAsGoal).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("review-answer-remove")).toBeNull();
  });

  it("remove_only: „Herausnehmen?“ with the one remove answer", () => {
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "A", resolver: "remove_only" } as Partial<Verdict>) })} />, "de"));
    expect(screen.getByTestId("review-card-question").textContent).toBe("Herausnehmen?");
    expect(screen.getAllByRole("button").filter((b) => b.dataset.testid?.startsWith("review-answer-"))).toHaveLength(1);
  });

  it("cl. 12: the card never shows the server's English detail", () => {
    render(withIntl(<ReviewSurface {...props()} />, "de"));
    expect(screen.getByTestId("review-surface").textContent ?? "").not.toContain("No vault evidence");
  });
});

describe("ADR-099 cl. 2 / 4 — classes", () => {
  it("F is hidden on a clean loop and counted in the passed line", () => {
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "F", resolver: "ask_true" } as Partial<Verdict>), atsReport: ats({ loop_clean: true, removals: [], fixed: ["cv-language"] }) })} />, "de"));
    expect(screen.queryByTestId("review-card")).toBeNull();
    expect(count()).toBe("0");
    expect(screen.getByTestId("review-passed-checks").textContent).toMatch(/^2 Prüfungen bestanden/);
  });

  it("F is NOT hidden when the loop did not finish clean (loop_clean null) — it falls back to its card", () => {
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "F", resolver: "ask_true" } as Partial<Verdict>), atsReport: ats({ loop_clean: null, removals: [] }) })} />, "de"));
    expect(screen.getByTestId("review-card")).toBeTruthy();
    expect(count()).toBe("1");
  });

  it("S never renders and never counts", () => {
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "S", resolver: null } as Partial<Verdict>) })} />, "de"));
    expect(screen.queryByTestId("review-card")).toBeNull();
    expect(count()).toBe("0");
    expect(screen.getByTestId("review-verdict").textContent).toBe("Nichts braucht Dich mehr – das Dokument ist versandbereit.");
  });

  it("a U check is one counted „Nicht geprüft“ row with „Jetzt prüfen“", async () => {
    api.recheckDocument.mockResolvedValue({ report: null, truthfulness: null, review_state: null });
    const checks = [{ id: "cv-language", status: "fail" as const, delivery_class: "U" as const, resolver: "recheck" }];
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "S" } as Partial<Verdict>), atsReport: ats(undefined, checks) })} />, "de"));
    const box = screen.getByTestId("review-unknown");
    expect(box.textContent).toContain("Nicht geprüft");
    expect(count()).toBe("1");
    fireEvent.click(within(box).getByTestId("review-unknown-recheck"));
    await waitFor(() => expect(api.recheckDocument).toHaveBeenCalledWith("cv", "cv-1"));
  });
});

describe("ADR-099 cl. 3 — the page's own cards count in the same number", () => {
  it("a counted extra card adds to the headline; an uncounted one does not", () => {
    render(
      withIntl(
        <ReviewSurface
          {...props({
            extraCards: [
              { key: "signature", counted: true, node: <p>signature card</p> },
              { key: "note", counted: false, node: <p>note</p> },
            ],
          })}
        />,
        "de",
      ),
    );
    expect(count()).toBe("2");
    expect(screen.getByTestId("review-extra-signature").dataset.counted).toBe("true");
    expect(screen.getByTestId("review-extra-note").dataset.counted).toBe("false");
    expect(screen.getByTestId("review-verdict").textContent).toBe("2 Punkte brauchen Dich, bevor Du sendest.");
  });
});

describe("ADR-099 cl. 5 — the removal line", () => {
  const removals = (pinned: boolean) => ({
    loop_clean: true,
    removals: [
      { text: "Leitete 12 Personen.", kind: "unbacked", section: "work_history[0]", pinned: false },
      { text: "SAP S/4HANA-Migration verantwortet.", kind: "unbacked", section: "work_history[1]", pinned },
    ],
    fixed: [],
  });

  it("collapsed by default: one line with the count, items hidden, never counted", () => {
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "S" } as Partial<Verdict>), atsReport: ats(removals(false)) })} />, "de"));
    const line = screen.getByTestId("review-removals");
    expect(line.dataset.expanded).toBe("false");
    expect(line.textContent).toContain("2 Stellen");
    expect(screen.queryAllByTestId("review-removal-item")).toHaveLength(0);
    expect(count()).toBe("0");
    fireEvent.click(screen.getByTestId("review-removals-toggle"));
    expect(screen.getAllByTestId("review-removal-item")).toHaveLength(2);
  });

  it("a removed PINNED fact forces the line open and is marked", () => {
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "S" } as Partial<Verdict>), atsReport: ats(removals(true)) })} />, "de"));
    expect(screen.getByTestId("review-removals").dataset.expanded).toBe("true");
    const pinned = screen.getAllByTestId("review-removal-item").find((i) => i.dataset.pinned === "true")!;
    expect(pinned.textContent).toContain("Festgelegter Fakt");
  });

  it("„Doch, stimmt – wo?“ sends the user's own words to the removal-evidence door", async () => {
    api.removalEvidence.mockResolvedValue({ testimony: { status: "applied", changes: [{}] }, review_state: null });
    render(withIntl(<ReviewSurface {...props({ truthReport: truth({ delivery_class: "S" } as Partial<Verdict>), atsReport: ats(removals(false)) })} />, "de"));
    fireEvent.click(screen.getByTestId("review-removals-toggle"));
    fireEvent.click(screen.getAllByTestId("review-removal-where")[0]);
    fireEvent.change(screen.getByTestId("review-removal-text"), { target: { value: "Bei Nordwind, Team von 12." } });
    fireEvent.click(screen.getByTestId("review-removal-submit"));
    await waitFor(() =>
      expect(api.removalEvidence).toHaveBeenCalledWith("cv", "cv-1", "Leitete 12 Personen.", "Bei Nordwind, Team von 12."),
    );
  });
});

describe("ADR-099 cl. 7 — page length has ONE place, counted once", () => {
  const pageCheck = {
    id: "page-length",
    status: "fail" as const,
    details_key: "page-length-letter",
    details_params: { pages: 2, letterPages: 1 },
    delivery_class: "A" as const,
    resolver: "page_length",
  };
  const clean: TruthfulnessReport = { version: "2", document_kind: "cv", counts: {}, stated_limit: "", claims: [] };

  it("the letter's card: title, both answers labelled, the filled one without a light background, counted once", () => {
    render(withIntl(<ReviewSurface {...props({ documentKind: "cover-letter", documentId: "cl-1", truthReport: clean, atsReport: ats(undefined, [pageCheck]) })} />, "de"));
    const card = screen.getByTestId("review-letter-page");
    expect(card.textContent).toContain("Dein Anschreiben braucht 2 Seiten statt 1 Seite");
    expect(within(card).getByTestId("review-letter-page-keep").textContent).toBe("2 Seiten behalten");
    const cut = within(card).getByTestId("review-letter-page-cut");
    expect(cut.textContent).toBe("Auf 1 Seite kürzen");
    // Tailwind resolves bg-*/text-* conflicts by stylesheet order: a filled
    // button that also carries the outline's light background renders white on white.
    expect(cut.className).toContain("bg-primary");
    expect(cut.className).not.toMatch(/bg-surface|text-on-surface/);
    expect(count()).toBe("1");
  });

  it("„kürzen“ calls the letter's shorten door; „behalten“ the keep door", async () => {
    api.shortenLetter.mockResolvedValue({ changes: [{ section_id: "body", before: "A. B.", after: "A." }], still_listed: false, report: ats(), truthfulness: null, review_state: null });
    api.keepLetterLength.mockResolvedValue({ report: ats(), truthfulness: null, review_state: null });
    const p = props({ documentKind: "cover-letter", documentId: "cl-1", truthReport: clean, atsReport: ats(undefined, [pageCheck]) });
    const { unmount } = render(withIntl(<ReviewSurface {...p} />, "de"));
    fireEvent.click(screen.getByTestId("review-letter-page-cut"));
    await waitFor(() => expect(api.shortenLetter).toHaveBeenCalledWith("cl-1"));
    await waitFor(() => expect(screen.getByTestId("review-letter-page-result")).toBeTruthy());
    unmount();
    render(withIntl(<ReviewSurface {...p} />, "de"));
    fireEvent.click(screen.getByTestId("review-letter-page-keep"));
    await waitFor(() => expect(api.keepLetterLength).toHaveBeenCalledWith("cl-1"));
  });

  it("on the CV the page's own card is the ONE page card: the report row adds no second card and no second count", () => {
    const pageCard = { key: "page-length", counted: true, node: <div data-testid="cv-page-card-stub" /> };
    render(withIntl(<ReviewSurface {...props({ truthReport: clean, atsReport: ats(undefined, [{ ...pageCheck, details_key: "page-length-cv" }]), extraCards: [pageCard] })} />, "de"));
    expect(screen.queryByTestId("review-letter-page")).toBeNull();
    expect(screen.getAllByTestId("cv-page-card-stub")).toHaveLength(1);
    expect(count()).toBe("1");
  });
});
