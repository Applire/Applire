// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * MD-G36 fix round 3, item 6 (delivery re-probe on e7e7cfa3): the Oracle judged 17 of
 * 20 claims, 3 got no usable judgement and stay so after "Jetzt prüfen"; nothing is
 * held and the agent door counts 0. The headline said "1 Punkt braucht Dich" — only
 * the "Nicht geprüft: Wahrheits-Orakel" row. That row is information: shown, never
 * counted. The state is a CAPTURED served response (`get_cv_ats_report` +
 * `get_cv_truthfulness_report`), written by
 * `tests/unit/test_dh_fix3_served_partial_judgement.py` — never by hand.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
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
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import served from "@/lib/__tests__/fixtures/served-partial-judgement-cv.json";

const CRITIC = { ran: true, mount: "cv" as const, advisories: [], dropped_citations: 0 };
const atsReport = served.ats.report as unknown as ATSReport;
const truthReport = served.truth as unknown as TruthfulnessReport;

describe("item 6 — a partial Oracle judgement is shown, not counted", () => {
  it("the view: the Oracle row is listed as information and the count is 0 (the agent door's number)", () => {
    const view = buildDeliveryView({ atsReport, truthReport, criticReport: CRITIC, gapClusters: [] });
    const oracle = view.unknown.find((u) => u.producer === "oracle");
    expect(oracle?.counted).toBe(false);
    expect(view.count).toBe(0);
  });

  it("rendered: no 'braucht Dich' headline, the row is still on screen", () => {
    render(
      <NextIntlClientProvider locale="de" messages={de}>
        <ReviewSurface documentKind="cv" documentId="00000000-0000-4000-8000-0000000000c3" atsReport={atsReport}
          truthReport={truthReport} criticReport={CRITIC} gapClusters={[]} gapAnalysisHref="/flow/f1/gaps" />
      </NextIntlClientProvider>,
    );
    expect(Number(screen.getByTestId("review-verdict").dataset.count)).toBe(0);
    expect(screen.getByTestId("review-verdict").textContent).toBe("Nichts braucht Dich mehr – das Dokument ist versandbereit.");
    const row = screen.getByTestId("review-unknown");
    expect(row.dataset.counted).toBe("false");
    expect(row.textContent).toContain("Wahrheits");
  });

  it("not run at all (no report) still counts", () => {
    const view = buildDeliveryView({ atsReport, truthReport: null as unknown as TruthfulnessReport, criticReport: CRITIC, gapClusters: [] });
    expect(view.unknown.find((u) => u.producer === "oracle")?.counted).not.toBe(false);
    expect(view.count).toBe(1);
  });

  it("nothing judged (every checkable claim unavailable) still counts", () => {
    const all = { ...truthReport, judgement_unavailable: truthReport.claims.length };
    const view = buildDeliveryView({ atsReport, truthReport: all, criticReport: CRITIC, gapClusters: [] });
    expect(view.count).toBe(1);
  });
});
