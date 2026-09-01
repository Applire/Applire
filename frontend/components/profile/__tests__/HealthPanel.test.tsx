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

// #626 — a conflict whose `entity_id` resolves: the structured fields
// `_conflict_issue` (services/profile/health.py) now populates.
const REVIEW_HEALTH: ProfileHealth = {
  issues: [
    {
      id: "conflict:abc",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary: "Senior Developer @ Acme Corp: work_experience.start_date: '2020-01' vs '2019-06'",
      field_ref: "start_date",
      source_record_ref: "cv:audi.pdf",
      entity_label: "Senior Developer @ Acme Corp",
      section: "work_experience",
      field: "start_date",
      existing_value_display: "2020-01",
      incoming_value_display: "2019-06",
      existing_source: null,
      incoming_source: "cv_upload",
    },
  ],
  completeness: { score: 0.82, gaps: ["certifications"], field_gaps: ["achievements: Team Lead @ Acme"] },
};

// #626 — a profile-level conflict (e.g. `professional_summary`): `entity_id`
// was never set (#218 — no entity to name), so `entity_label` is legitimately
// `null`. Must render a sensible general heading, never "null"/"undefined".
const PROFILE_LEVEL_CONFLICT_HEALTH: ProfileHealth = {
  issues: [
    {
      id: "conflict:summary-de",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary: "professional_summary.de: 'Alte Zusammenfassung' vs 'Neue Zusammenfassung'",
      field_ref: "de",
      source_record_ref: "cv_upload",
      entity_label: null,
      section: "professional_summary",
      field: "de",
      existing_value_display: "Alte Zusammenfassung",
      incoming_value_display: "Neue Zusammenfassung",
      existing_source: null,
      incoming_source: "cv_upload",
    },
  ],
  completeness: { score: 0.9, gaps: [], field_gaps: [] },
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

// #333 — a parked reconciler ambiguity (testimony or import). Its "Resolve" is
// the only route into the profile-review interview that can answer it.
const CONFIRMATION_HEALTH: ProfileHealth = {
  issues: [
    {
      id: "confirmation:c-1",
      thread: "confirmation",
      profile_mismatch_severity: "review",
      summary: "Is 'Projektleiter' the same role as 'Project Lead'?",
      field_ref: null,
      source_record_ref: "testimony",
    },
  ],
  completeness: { score: 0.9, gaps: [], field_gaps: [] },
};

// #382 (PO decision 2026-08-08, Option A) — a budget figure with no unit is
// omitted from every generated document. The omission must never be silent, so
// it arrives here as its own thread. The panel translates it rather than
// printing the backend's summary: the user is being ASKED something, in their
// own language, not shown a data-quality log line.
const UNIT_HEALTH: ProfileHealth = {
  issues: [
    {
      id: "unit:budget_managed:Produktionsleiter @ Weberit GmbH",
      thread: "unit",
      profile_mismatch_severity: "review",
      summary: "budget_managed: '6000000' states no unit, so it is omitted",
      field_ref: "work_experience.budget_managed",
      source_record_ref: "Produktionsleiter @ Weberit GmbH",
    },
  ],
  completeness: { score: 0.8, gaps: [], field_gaps: [] },
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
  // #626 — the reported defect, verbatim: a conflict named the FIELD but never
  // the ENTRY it belonged to. The raw "work_experience.start_date: 'x' vs 'y'"
  // backend string must never reach the user; the entry, the field in words,
  // and both values with their provenance must.
  it("renders a conflict issue naming its entry, the field in words, and both values with provenance (en)", () => {
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} />, "en"));

    const cards = screen.getAllByTestId("health-issue");
    expect(cards).toHaveLength(1);
    // The entry is named — this is the fix.
    expect(screen.getByText(/Senior Developer @ Acme Corp/)).toBeInTheDocument();
    // The field is in human words, not the raw dotted machine key.
    expect(screen.getByText(/Start date/)).toBeInTheDocument();
    expect(screen.queryByText(/work_experience\.start_date/)).not.toBeInTheDocument();
    // Both values are shown, each labeled with its provenance.
    expect(screen.getByText(/Current value/)).toBeInTheDocument();
    expect(screen.getByText(/2020-01/)).toBeInTheDocument();
    expect(screen.getByText(/New value from/)).toBeInTheDocument();
    expect(screen.getByText(/CV upload/)).toBeInTheDocument();
    expect(screen.getByText(/2019-06/)).toBeInTheDocument();
    // Thread + severity labels are translated chrome, not raw enum values.
    expect(screen.getByText(/Conflict/i)).toBeInTheDocument();
    expect(screen.getByText(/Review/i)).toBeInTheDocument();
  });

  // #626 — a profile-level conflict (no entity to name) must not crash and
  // must not print "null"/"undefined" — it degrades to a general heading.
  it("renders a profile-level conflict (no entity_id) with a general heading, never a null/undefined label", () => {
    render(withIntl(<HealthPanel health={PROFILE_LEVEL_CONFLICT_HEALTH} onResolve={vi.fn()} />, "en"));

    expect(screen.getAllByTestId("health-issue")).toHaveLength(1);
    expect(screen.queryByText(/null/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/undefined/i)).not.toBeInTheDocument();
    // `professional_summary` + field "de" is special-cased to a real label.
    expect(screen.getByText(/Summary \(German\)/)).toBeInTheDocument();
    expect(screen.getByText(/Alte Zusammenfassung/)).toBeInTheDocument();
    expect(screen.getByText(/Neue Zusammenfassung/)).toBeInTheDocument();
  });

  it("renders a conflict issue in German with translated field/value labels", () => {
    render(withIntl(<HealthPanel health={REVIEW_HEALTH} onResolve={vi.fn()} />, "de"));

    expect(screen.getByText(/Senior Developer @ Acme Corp/)).toBeInTheDocument();
    expect(screen.getByText(/Startdatum/)).toBeInTheDocument();
    expect(screen.getByText(/Aktueller Wert/)).toBeInTheDocument();
    expect(screen.getByText(/Neuer Wert aus/)).toBeInTheDocument();
    expect(screen.getByText(/Lebenslauf-Upload/)).toBeInTheDocument();
  });

  it("renders a parked confirmation as a resolvable card with a translated thread label", () => {
    const onResolve = vi.fn();
    const { unmount } = render(
      withIntl(<HealthPanel health={CONFIRMATION_HEALTH} onResolve={onResolve} />, "en"),
    );

    expect(screen.getAllByTestId("health-issue")).toHaveLength(1);
    expect(screen.getByText(/Projektleiter/)).toBeInTheDocument();
    expect(screen.getByText("Open question")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("health-resolve"));
    expect(onResolve).toHaveBeenCalledWith(CONFIRMATION_HEALTH.issues[0]);
    unmount();

    render(
      withIntl(<HealthPanel health={CONFIRMATION_HEALTH} onResolve={vi.fn()} />, "de"),
    );
    expect(screen.getByText("Offene Rückfrage")).toBeInTheDocument();
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

  it("renders a unit-thread issue with a translated ask, not the backend summary", () => {
    render(withIntl(<HealthPanel health={UNIT_HEALTH} onResolve={vi.fn()} />, "en"));

    expect(screen.getAllByTestId("health-issue")).toHaveLength(1);
    // The affected entry is named, so the user knows WHICH budget is meant.
    expect(screen.getByText(/Produktionsleiter @ Weberit GmbH/)).toBeInTheDocument();
    // The raw backend wording never reaches the user.
    expect(screen.queryByText(/states no unit/)).not.toBeInTheDocument();
  });

  it("is resolvable like any other issue — the omission is never a dead end", () => {
    const onResolve = vi.fn();
    render(withIntl(<HealthPanel health={UNIT_HEALTH} onResolve={onResolve} />, "en"));

    fireEvent.click(screen.getByTestId("health-resolve"));
    expect(onResolve).toHaveBeenCalledWith(UNIT_HEALTH.issues[0]);
  });
});
