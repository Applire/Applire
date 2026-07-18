// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import TruthfulnessPanel, { type TruthfulnessReport } from "../TruthfulnessPanel";

const REPORT_WITH_FLAGS: TruthfulnessReport = {
  version: "1.0",
  document_kind: "cv",
  claims: [
    {
      claim: { text: "Reduced manual effort by 70%.", location: "summary[0]", kind: "sentence" },
      verdict: {
        verdict: "inflated",
        checker: "stance",
        evidence: [
          {
            kind: "profile_path",
            ref: "work_experience[0].achievements[0]",
            excerpt: "targets a ~70% reduction in manual effort",
          },
          { kind: "enrichment_record", ref: "rec-1" },
        ],
        detail: "Rendered as an achieved outcome, but the vault evidence is aspirational.",
      },
    },
    {
      claim: { text: "React Native", location: "skills[3]", kind: "skill" },
      verdict: {
        verdict: "unbacked",
        checker: "grounding",
        evidence: [],
        detail: 'Skill "React Native" has no vault evidence.',
      },
    },
    {
      claim: { text: "Cut deployment time by 40%.", location: "work_history[0].bullets[0]", kind: "bullet" },
      verdict: { verdict: "grounded", checker: "numbers", evidence: [], detail: null },
    },
  ],
  counts: { grounded: 1, inflated: 1, unbacked: 1, unverifiable: 0 },
  stated_limit: "This report verifies document-vault consistency only.",
};

const ALL_GREEN_REPORT: TruthfulnessReport = {
  version: "1.0",
  document_kind: "cv",
  claims: [
    {
      claim: { text: "Cut deployment time by 40%.", location: "work_history[0].bullets[0]", kind: "bullet" },
      verdict: { verdict: "grounded", checker: "numbers", evidence: [], detail: null },
    },
    {
      claim: { text: "Passionate engineering leader.", location: "summary[0]", kind: "sentence" },
      verdict: { verdict: "unverifiable", checker: "grounding", evidence: [], detail: null },
    },
  ],
  counts: { grounded: 1, inflated: 0, unbacked: 0, unverifiable: 1 },
  stated_limit: "This report verifies document-vault consistency only.",
};

describe("TruthfulnessPanel", () => {
  it("renders the unavailable state for a null report", () => {
    render(withIntl(<TruthfulnessPanel report={null} />));
    expect(screen.getByTestId("truthfulness-unavailable")).toBeInTheDocument();
  });

  it("keeps red flags loud on the compact card", () => {
    render(withIntl(<TruthfulnessPanel report={REPORT_WITH_FLAGS} />));
    expect(screen.getByTestId("truthfulness-status")).toBeInTheDocument();
    // both flagged claims render inline without any interaction
    expect(screen.getByTestId("truthfulness-flag-summary[0]")).toBeInTheDocument();
    expect(screen.getByTestId("truthfulness-flag-skills[3]")).toBeInTheDocument();
    expect(screen.getByText("Reduced manual effort by 70%.")).toBeInTheDocument();
    // the grounded claim does NOT clutter the compact card
    expect(screen.queryByText("Cut deployment time by 40%.")).not.toBeInTheDocument();
  });

  it("all-green state: no flags, unverifiable is one muted note (no wall of warnings)", () => {
    render(withIntl(<TruthfulnessPanel report={ALL_GREEN_REPORT} />));
    expect(screen.getByTestId("truthfulness-unverifiable-note")).toBeInTheDocument();
    expect(screen.queryByTestId(/truthfulness-flag-/)).not.toBeInTheDocument();
    // the unverifiable claim text itself is not rendered on the card
    expect(screen.queryByText("Passionate engineering leader.")).not.toBeInTheDocument();
  });

  it("drawer lists every claim with location, evidence excerpt, and the stated limit", () => {
    render(withIntl(<TruthfulnessPanel report={REPORT_WITH_FLAGS} />));
    fireEvent.click(screen.getByTestId("truthfulness-details-button"));
    expect(screen.getByTestId("truthfulness-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("truthfulness-drawer-claim-summary[0]")).toBeInTheDocument();
    expect(
      screen.getByTestId("truthfulness-drawer-claim-work_history[0].bullets[0]"),
    ).toBeInTheDocument();
    // location + profile evidence excerpt are visible
    expect(screen.getByText("summary[0]")).toBeInTheDocument();
    expect(
      screen.getByText("targets a ~70% reduction in manual effort"),
    ).toBeInTheDocument();
    // ADR-052 §5: the stated limit always renders with the full report
    expect(screen.getByTestId("truthfulness-stated-limit")).toBeInTheDocument();
  });

  it("drawer closes via the close button", () => {
    render(withIntl(<TruthfulnessPanel report={REPORT_WITH_FLAGS} />));
    fireEvent.click(screen.getByTestId("truthfulness-details-button"));
    fireEvent.click(screen.getByTestId("truthfulness-drawer-close"));
    expect(screen.queryByTestId("truthfulness-drawer")).not.toBeInTheDocument();
  });
});
