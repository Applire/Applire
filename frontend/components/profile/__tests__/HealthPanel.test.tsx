// Copyright (C) 2024-2026 Tobias Rosenbaum
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

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { HealthPanel, type ProfileHealth } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const REVIEW_HEALTH: ProfileHealth = {
  issues: [
    {
      id: "conflict:abc",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary: "work_experience.start_date: '2020-01' vs '2019-06'",
      field_ref: "start_date",
      source_record_ref: "cv:audi.pdf",
    },
  ],
  completeness: { score: 0.82, gaps: ["certifications"], field_gaps: ["achievements: Team Lead @ Acme"] },
};

const CRITICAL_HEALTH: ProfileHealth = {
  issues: [
    {
      id: "accuracy:xyz",
      thread: "accuracy",
      profile_mismatch_severity: "critical",
      summary: "Merge from cv_upload did not retain 7 extracted item(s)",
      field_ref: "work_experience",
      source_record_ref: "rec-1",
    },
  ],
  completeness: { score: 0.5, gaps: [], field_gaps: [] },
};

const HEALTHY: ProfileHealth = {
  issues: [],
  completeness: { score: 1.0, gaps: [], field_gaps: [] },
};

// US179 — section gaps present but no field gaps → button disabled
const SECTION_GAPS_NO_FIELD_GAPS: ProfileHealth = {
  issues: [],
  completeness: { score: 0.9, gaps: ["education"], field_gaps: [] },
};

// US179 — two field gaps → button enabled and count reflects field_gaps length
const TWO_FIELD_GAPS: ProfileHealth = {
  issues: [],
  completeness: { score: 0.75, gaps: [], field_gaps: ["achievements: X @ Y", "team_size: X @ Y"] },
};

describe("HealthPanel", () => {
  it("renders a card per health issue with thread, severity and summary (en)", () => {
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} />, "en"));

    const cards = screen.getAllByTestId("health-issue");
    expect(cards).toHaveLength(1);
    expect(screen.getByText(/work_experience\.start_date/)).toBeInTheDocument();
    // Thread + severity labels are translated chrome, not raw enum values.
    expect(screen.getByText(/Conflict/i)).toBeInTheDocument();
    expect(screen.getByText(/Review/i)).toBeInTheDocument();
  });

  it("calls onResolve with the issue when Resolve is clicked", () => {
    const onResolve = vi.fn();
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={onResolve} />, "en"));

    fireEvent.click(screen.getByTestId("health-resolve"));
    expect(onResolve).toHaveBeenCalledWith(REVIEW_HEALTH.issues[0]);
  });

  it("shows a dismissible nudge for review/info issues that can be hidden", () => {
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} />, "en"));

    expect(screen.getByTestId("health-nudge")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("health-nudge-dismiss"));
    expect(screen.queryByTestId("health-nudge")).not.toBeInTheDocument();
    // Dismissing the nudge never removes the underlying issue card (nudge-not-gate).
    expect(screen.getAllByTestId("health-issue")).toHaveLength(1);
  });

  it("shows critical issues prominently and never as a dismissible nudge", () => {
    render(withIntl(<HealthPanel health={CRITICAL_HEALTH} onResolve={vi.fn()} />, "en"));

    expect(screen.getByTestId("health-critical")).toBeInTheDocument();
    // A critical-only profile gets no review/info nudge.
    expect(screen.queryByTestId("health-nudge")).not.toBeInTheDocument();
  });

  it("renders an all-clear summary and no nudge when there are no issues", () => {
    render(withIntl(<HealthPanel health={HEALTHY} onResolve={vi.fn()} />, "en"));

    expect(screen.queryByTestId("health-issue")).not.toBeInTheDocument();
    expect(screen.queryByTestId("health-nudge")).not.toBeInTheDocument();
    expect(screen.getByTestId("health-panel")).toBeInTheDocument();
  });

  it("renders German chrome under the de locale", () => {
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} />, "de"));
    expect(screen.getByText("Profilzustand")).toBeInTheDocument();
  });

  // US166 — the completeness block is the in-context launch point for the
  // standalone Mode C (ADR-028) enrichment conversation (growth, not correctness).
  it("renders an Improve action in the completeness block when gaps exist and calls onImprove", () => {
    const onImprove = vi.fn();
    render(
      withIntl(
        <HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} onImprove={onImprove} />,
        "en",
      ),
    );

    const improve = screen.getByTestId("health-improve");
    expect(improve).toBeInTheDocument();
    fireEvent.click(improve);
    expect(onImprove).toHaveBeenCalledTimes(1);
  });

  it("renders no Improve action when there are no completeness gaps", () => {
    render(
      withIntl(<HealthPanel health={CRITICAL_HEALTH} onResolve={vi.fn()} onImprove={vi.fn()} />, "en"),
    );
    expect(screen.queryByTestId("health-improve")).not.toBeInTheDocument();
  });

  it("renders no Improve action when onImprove is not provided", () => {
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} />, "en"));
    expect(screen.queryByTestId("health-improve")).not.toBeInTheDocument();
  });

  it("labels the Improve action in German under the de locale", () => {
    render(
      withIntl(
        <HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} onImprove={vi.fn()} />,
        "de",
      ),
    );
    expect(screen.getByText("Verbessern")).toBeInTheDocument();
  });

  // US179 — enrich entry-point gates on field_gaps, not section gaps
  it("disables/hides the Improve button when section gaps exist but field_gaps is empty", () => {
    render(
      withIntl(
        <HealthPanel
          health={SECTION_GAPS_NO_FIELD_GAPS}
          onResolve={vi.fn()}
          onImprove={vi.fn()}
        />,
        "en",
      ),
    );
    expect(screen.queryByTestId("health-improve")).not.toBeInTheDocument();
  });

  it("shows the Improve button when field_gaps is non-empty, even if section gaps is empty", () => {
    render(
      withIntl(
        <HealthPanel
          health={TWO_FIELD_GAPS}
          onResolve={vi.fn()}
          onImprove={vi.fn()}
        />,
        "en",
      ),
    );
    expect(screen.getByTestId("health-improve")).toBeInTheDocument();
  });
});
