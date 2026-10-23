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
import { render, screen, within, cleanup } from "@testing-library/react";
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

  it("the headline names 2 decisions and the group rows mark exactly those 2", () => {
    renderDaniel();
    expect(screen.getByTestId("review-verdict").textContent).toBe(
      "2 points need your decision before you send it.",
    );
    expect(chipNumber(2)).toBe(1);
    expect(chipNumber(4)).toBe(1);
    expect(chipNumber(3)).toBe(0);
    expect(chipNumber(2) + chipNumber(3) + chipNumber(4)).toBe(headlineNumber());
  });

  it("the section line no longer says none of them matter, and names the count", () => {
    renderDaniel();
    const other = screen.getByTestId("review-other");
    expect(other.textContent).not.toContain("None of these stand in the way");
    expect(within(other).getByTestId("review-other-sub").textContent).toContain("2");
  });

  it("German: the same count reaches the section line and the chips", () => {
    renderDaniel("de");
    expect(screen.getByTestId("review-verdict").textContent).toMatch(/^2 Punkte brauchen Deine Entscheidung/);
    expect(screen.getByTestId("review-other-sub").textContent).not.toContain("Nichts davon steht dem Versand im Weg");
    expect(screen.getByTestId("review-group-decisions-4").textContent).toMatch(/1/);
  });

  it("without a decision the section line keeps its reassurance and no chip renders", () => {
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
    expect(screen.getByTestId("review-other-sub").textContent).toBe(
      "None of these stand in the way of sending the document.",
    );
    expect(screen.queryByTestId("review-group-decisions-2")).toBeNull();
    expect(screen.queryByTestId("review-group-decisions-4")).toBeNull();
  });
});
