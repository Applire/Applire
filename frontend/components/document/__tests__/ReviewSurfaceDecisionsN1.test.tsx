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

// Hazelnut build 3 re-probe N1 — the review headline counts decisions the panel
// never showed as decisions. T-1 (ADR-081 cl. 4 amended) counts every group-2 row
// and every failed craft check as "needs your decision"; the panel listed those
// rows under "Other findings — None of these stand in the way of sending the
// document." with nothing marking which ones the headline meant. Built on the
// persisted review inputs of the re-probe's CV (Daniel: 1 claimable keyword +
// the failed narrative-evidence check = 2).

import React from "react";
import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface } from "../ReviewSurface";
import type { ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { buildReviewGroups, decisionCount, groupDecisionCount } from "@/lib/review-groups";
import { DANIEL_REPROBE_CV } from "./fixtures/reprobe-daniel-cv";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const atsReport = DANIEL_REPROBE_CV.ats_report as unknown as ATSReport;
const truthReport = DANIEL_REPROBE_CV.truthfulness_report as unknown as TruthfulnessReport;
const criticReport = DANIEL_REPROBE_CV.critic_report as unknown as OutcomeCriticReport;

function renderDaniel(locale: "en" | "de" = "en") {
  return render(
    withIntl(
      <ReviewSurface
        documentKind="cv"
        documentId="reprobe-daniel-cv"
        atsReport={atsReport}
        truthReport={truthReport}
        criticReport={criticReport}
        gapClusters={[]}
        gapAnalysisHref="/flow/f1/gaps"
      />,
      locale,
    ),
  );
}

afterEach(cleanup);

const headlineNumber = () => Number(screen.getByTestId("review-verdict").textContent?.match(/\d+/)?.[0]);
const chipNumber = (id: number) =>
  Number(screen.queryByTestId(`review-group-decisions-${id}`)?.textContent?.match(/\d+/)?.[0] ?? 0);

describe("re-probe N1 — the panel shows every decision the headline counts", () => {
  it("the fixture has the shape the defect needs (1 claimable term + 1 failed craft check)", () => {
    const groups = buildReviewGroups({ atsReport, truthReport, criticReport, gapClusters: [] });
    expect(groups.find((g) => g.id === 1)!.items).toHaveLength(0);
    expect(decisionCount(groups)).toBe(2);
    expect(groups.map((g) => groupDecisionCount(g))).toEqual([0, 1, 0, 1]);
  });

  // ADR-099 cl. 3/8: the headline counts exactly the counted rows the surface
  // renders. The claimable term (old group 2) and the craft section left the
  // surface; on this PRE-ADR-099 report (no delivery class stamped) the failed
  // check is the honest unclassified fallback — one counted row, label only.
  it("ADR-099: the headline count equals the counted rows the panel renders", () => {
    renderDaniel();
    const counted = document.querySelectorAll('[data-counted="true"]').length;
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe(String(counted));
    expect(headlineNumber()).toBe(counted);
    expect(counted).toBe(1);
    expect(screen.getByTestId("review-fallback-narrative-evidence").getAttribute("data-counted")).toBe("true");
  });

  it("no group 2, no group 4, no 'other findings' section any more", () => {
    renderDaniel();
    expect(screen.queryByTestId("review-other")).toBeNull();
    expect(screen.queryByTestId("review-other-sub")).toBeNull();
    expect(chipNumber(2) + chipNumber(4)).toBe(0);
    expect(screen.queryByTestId("review-group-toggle-2")).toBeNull();
  });

  it("German: the same count, in the ADR-099 headline", () => {
    renderDaniel("de");
    expect(screen.getByTestId("review-verdict").textContent).toBe("1 Punkt braucht Dich, bevor Du sendest.");
  });

  it("without the failed check nothing is counted and the clear line shows", () => {
    render(
      withIntl(
        <ReviewSurface
          documentKind="cv"
          documentId="reprobe-daniel-cv"
          atsReport={{
            ...atsReport,
            keywords: { ...atsReport!.keywords, missing_claimable: [] },
            checks: atsReport!.checks.filter((c) => c.status !== "fail"),
          } as ATSReport}
          truthReport={truthReport}
          criticReport={criticReport}
          gapClusters={[]}
          gapAnalysisHref="/flow/f1/gaps"
        />,
      ),
    );
    expect(document.querySelectorAll('[data-counted="true"]')).toHaveLength(0);
    expect(screen.getByTestId("review-verdict").getAttribute("data-count")).toBe("0");
  });
});
