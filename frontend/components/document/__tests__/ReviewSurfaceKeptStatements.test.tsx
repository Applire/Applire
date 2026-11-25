// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * MD-G36 fix round 3 (X-2b, adv re-probe 1 on e7e7cfa3): after "Passt so" the headline,
 * tab and phone said 1 while the panel still showed the kept statements as open
 * "Stimmt das?" cards labelled "1 VON 8". One rule for the decided state: a statement
 * kept through the card is not asked again and not counted; the card's "… von Dir so
 * gelassen. Rückgängig" line is what remains of it; "n VON m" counts only cards shown.
 * Undo brings the cards and the count back together.
 *
 * Both states are CAPTURED served responses (`get_cv_ats_report` with the
 * review_state the keep/undo door writes), written by
 * `tests/unit/test_dh_fix3_served_decided.py` — never by hand.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";
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

import { ReviewSurface } from "../ReviewSurface";
import { buildDeliveryView } from "@/lib/review-groups";
import type { ATSReport } from "@/lib/ats-report";
import type { ReviewState } from "@/lib/api/document-review";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import servedKept from "@/lib/__tests__/fixtures/served-kept-cv.json";
import servedUndone from "@/lib/__tests__/fixtures/served-undone-cv.json";

const CRITIC = { ran: true, mount: "cv" as const, advisories: [], dropped_citations: 0 };

function inputs(served: typeof servedKept) {
  return {
    atsReport: served.ats.report as unknown as ATSReport,
    truthReport: served.truth as unknown as TruthfulnessReport,
    reviewState: served.ats.review_state as unknown as ReviewState,
  };
}

function view(served: typeof servedKept) {
  return buildDeliveryView({ ...inputs(served), criticReport: CRITIC, gapClusters: [] });
}

function withIntl(ui: ReactElement) {
  return <NextIntlClientProvider locale="de" messages={de}>{ui}</NextIntlClientProvider>;
}

function renderServed(served: typeof servedKept) {
  render(withIntl(
    <ReviewSurface documentKind="cv" documentId="00000000-0000-4000-8000-0000000000c1" {...inputs(served)}
      criticReport={CRITIC} gapClusters={[]} gapAnalysisHref="/flow/f1/gaps" />,
  ));
}

const cardKeys = (served: typeof servedKept) =>
  ((view(served).openPoints?.check.notes ?? []) as { finding_key?: string | null }[])
    .map((n) => n.finding_key).filter((k): k is string => !!k);

describe("X-2b — the decided state on every surface", () => {
  it("kept: the card's statements are not question cards, and the count is the translate card alone", () => {
    const v = view(servedKept);
    expect(v.openPoints?.acknowledged).toBe(true);
    const keys = cardKeys(servedKept);
    expect(keys).toHaveLength(3);
    for (const k of keys) expect(v.questions.map((q) => q.findingKey)).not.toContain(k);
    expect(v.count).toBe(1);
  });

  it("undone: the same statements are question cards again and counted through the card", () => {
    const v = view(servedUndone);
    expect(v.openPoints?.acknowledged).toBe(false);
    const qk = v.questions.map((q) => q.findingKey);
    for (const k of cardKeys(servedUndone)) expect(qk).toContain(k);
    expect(v.count).toBe(5 + 1);
  });

  it("kept, rendered: headline 1, the kept line with Rückgängig, no open 'n VON m' over the kept statements", () => {
    renderServed(servedKept);
    expect(Number(screen.getByTestId("review-verdict").dataset.count)).toBe(1);
    expect(screen.getByTestId("review-open-points").dataset.acknowledged).toBe("true");
    expect(screen.getByTestId("review-open-points-undo")).toBeTruthy();
    for (const el of screen.queryAllByTestId("review-card-position")) {
      const m = /(\d+) VON (\d+)/.exec(el.textContent ?? "");
      if (m) expect(Number(m[2])).toBeLessThanOrEqual(1);
    }
    expect(document.body.textContent).not.toContain("I led a team of 120 people at Nordhafen Logistik.");
  });

  it("undone, rendered: the headline counts the card again and the cards are back", () => {
    renderServed(servedUndone);
    expect(Number(screen.getByTestId("review-verdict").dataset.count)).toBe(6);
    expect(screen.getByTestId("review-open-points").dataset.acknowledged).toBe("false");
    expect(screen.getByTestId("review-card-position").textContent).toMatch(/^1 VON 6/);
  });
});
