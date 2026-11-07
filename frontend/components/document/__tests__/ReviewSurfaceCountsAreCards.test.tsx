// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * 789:259 / 789:260 (ux-audit-3) — the review panel's numbers are its cards.
 *
 * 259: "2 Punkte" over "1 VON 1" (Marcus) — the card position counted the
 * question deck, the headline every counted card. Now one number.
 * 260: "Nichts braucht Dich mehr" over "Fehlt, und Dein Profil deckt es nicht · 1"
 * that never said WHAT was missing (Felix, Priya). Now the card names it.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";
import en from "@/messages/en.json";
import de from "@/messages/de.json";

const api = vi.hoisted(() => ({
  addEvidence: vi.fn(), confirmSupported: vi.fn(), takeOut: vi.fn(), undoDecision: vi.fn(),
  markEdited: vi.fn(), markWalked: vi.fn().mockResolvedValue({ review_state: null }),
}));
vi.mock("@/lib/api/document-review", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/document-review")>();
  return { ...actual, ...api };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }), usePathname: () => "/" }));

import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";

function withIntl(ui: ReactElement, locale: "en" | "de" = "en") {
  return <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : de}>{ui}</NextIntlClientProvider>;
}

function ats(present_unsupported: string[]): ATSReport {
  return {
    score: 80, checks: [],
    keywords: {
      present: [], missing: [], missing_claimable: [], missing_honest_gap: [],
      present_unsupported, claimable_concepts: [],
    },
  } as unknown as ATSReport;
}

const BASE: ReviewSurfaceProps = {
  documentKind: "cv",
  documentId: "cv-1",
  atsReport: ats(["Lieferkette"]),
  truthReport: { version: "1", document_kind: "cv", claims: [], counts: {}, stated_limit: "" },
  criticReport: { ran: true, mount: "cv", advisories: [], dropped_citations: 0 },
  gapAnalysisHref: "/flow/f1/gaps",
};

const verdictCount = () => Number(screen.getByTestId("review-verdict").dataset.count);

describe("789:259 — the card position and the headline are one number", () => {
  it("one question + one counted page card: 2 points, and the card says 1 OF 2", () => {
    render(withIntl(<ReviewSurface {...BASE} extraCards={[{ key: "signature", counted: true, node: <div>sig</div> }]} />));
    expect(verdictCount()).toBe(2);
    expect(screen.getByTestId("review-card-position").textContent).toMatch(/^1 OF 2/);
  });

  it("an uncounted page card adds to neither", () => {
    render(withIntl(<ReviewSurface {...BASE} extraCards={[{ key: "info", counted: false, node: <div>i</div> }]} />));
    expect(verdictCount()).toBe(1);
    expect(screen.getByTestId("review-card-position").textContent).toMatch(/^1 OF 1/);
  });

  it("an employer-sentence row (#803, uncounted) shows no position", () => {
    const report = { ...ats(["Verbundverpackungen"]) } as ATSReport;
    (report.keywords as Record<string, unknown>).present_unsupported_employer_sentence = ["Verbundverpackungen"];
    render(withIntl(<ReviewSurface {...BASE} atsReport={report} />));
    expect(screen.getByTestId("review-card-position").textContent).not.toMatch(/OF/);
  });
});

describe("789:260 — the missing-items card names what is missing", () => {
  it("names up to three, then '+N'", () => {
    render(withIntl(<ReviewSurface {...BASE} atsReport={ats([])} openRequirements={["Payment settlement", "Kafka", "SBAR", "ECMO"]} />));
    const items = screen.getByTestId("review-group3-items").textContent ?? "";
    expect(items).toContain("Payment settlement, Kafka, SBAR");
    expect(items).toContain("and 1 more");
    expect(items).not.toContain("ECMO");
  });

  it("de: one item, named", () => {
    render(withIntl(<ReviewSurface {...BASE} atsReport={ats([])} openRequirements={["Payment settlement"]} />, "de"));
    expect(screen.getByTestId("review-group3-items").textContent).toBe("Payment settlement");
  });
});
