// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import TruthfulnessPanel, { type TruthfulnessReport } from "../TruthfulnessPanel";
import type { ATSReport } from "../ATSChecksPanel";

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

const MISATTRIBUTED_REPORT: TruthfulnessReport = {
  version: "1.1",
  document_kind: "cv",
  claims: [
    {
      claim: {
        text: "Led the FDA audit preparation programme.",
        location: "work_history[0].bullets[0]",
        kind: "bullet",
      },
      verdict: {
        verdict: "misattributed",
        checker: "attribution",
        evidence: [
          {
            kind: "profile_path",
            ref: "work_experience[1].achievements[0]",
            excerpt: "Led the FDA audit preparation programme for the Hamburg site.",
          },
        ],
        detail:
          "Backed only by evidence from a different position (work_experience[1].achievements[0]).",
      },
    },
  ],
  counts: { grounded: 0, inflated: 0, misattributed: 1, unbacked: 0, unverifiable: 0 },
  stated_limit: "This report verifies document-vault consistency only.",
};

// #237 (F14): a letter-shaped report where unverifiable dominates and NOTHING
// is flagged must not read as the green "everything backed" headline.
const UNVERIFIABLE_DOMINATED_REPORT: TruthfulnessReport = {
  version: "1.1",
  document_kind: "cover_letter",
  claims: [
    {
      claim: { text: "Cut deployment time by 40%.", location: "body.paragraphs[0][0]", kind: "sentence" },
      verdict: { verdict: "grounded", checker: "numbers", evidence: [], detail: null },
    },
    ...Array.from({ length: 8 }, (_, i) => ({
      claim: {
        text: `Soft formulaic claim number ${i}.`,
        location: `body.paragraphs[${i + 1}][0]`,
        kind: "sentence" as const,
      },
      verdict: {
        verdict: "unverifiable" as const,
        checker: "grounding" as const,
        evidence: [],
        detail: null,
      },
    })),
  ],
  counts: { grounded: 1, inflated: 0, misattributed: 0, unbacked: 0, unverifiable: 8 },
  stated_limit: "This report verifies document-vault consistency only.",
};

// E048/US266 (#249 option b): a skill claim the Oracle calls "unbacked" whose
// text matches a Keyword Ledger CLAIMABLE concept (the ATS report's new
// `claimable_concepts`, present or missing in the doc alike — this is
// adjacency evidence, not a literal vault hit) must render a third, honestly
// labeled state — never a red flag, never the plain green "backed" chip.
const UNBACKED_WITH_LEDGER_MATCH_REPORT: TruthfulnessReport = {
  version: "1.1",
  document_kind: "cv",
  claims: [
    {
      claim: { text: "Strategic Planning", location: "skills[0]", kind: "skill" },
      verdict: {
        verdict: "unbacked",
        checker: "grounding",
        evidence: [],
        detail: 'Skill "Strategic Planning" has no vault evidence.',
      },
    },
    {
      claim: { text: "Cut deployment time by 40%.", location: "work_history[0].bullets[0]", kind: "bullet" },
      verdict: { verdict: "grounded", checker: "numbers", evidence: [], detail: null },
    },
  ],
  counts: { grounded: 1, inflated: 0, unbacked: 1, unverifiable: 0 },
  stated_limit: "This report verifies document-vault consistency only.",
};

const ATS_WITH_CLAIMABLE_MATCH: ATSReport = {
  checks: [],
  keywords: {
    present: ["Strategic Planning"],
    missing: [],
    claimable_concepts: ["Strategic Planning", "Digital Strategy"],
  },
};

const ATS_WITHOUT_CLAIMABLE_MATCH: ATSReport = {
  checks: [],
  keywords: {
    present: [],
    missing: ["GraphQL"],
    claimable_concepts: ["GraphQL"],
  },
};

describe("TruthfulnessPanel — E048/US266 third-state skill/ledger join", () => {
  it("unbacked skill matching a claimable ledger concept renders the related-evidence state, not a red flag", () => {
    render(
      withIntl(
        <TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} atsReport={ATS_WITH_CLAIMABLE_MATCH} />,
      ),
    );
    // NOT in the loud red-flag list.
    expect(screen.queryByTestId("truthfulness-flag-skills[0]")).not.toBeInTheDocument();
    // Rendered instead as the distinct related-evidence chip.
    expect(screen.getByTestId("truthfulness-related-skills[0]")).toBeInTheDocument();
    expect(screen.getByTestId("truthfulness-chip-related").textContent).toBe("Related evidence");
    // Headline count excludes it — only the grounded claim, zero claims "need review".
    expect(screen.getByTestId("truthfulness-status").textContent).not.toContain("1 claim");
  });

  it("unbacked skill with NO matching claimable concept stays unbacked and red", () => {
    render(
      withIntl(
        <TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} atsReport={ATS_WITHOUT_CLAIMABLE_MATCH} />,
      ),
    );
    expect(screen.getByTestId("truthfulness-flag-skills[0]")).toBeInTheDocument();
    expect(screen.getByTestId("truthfulness-chip-unbacked")).toBeInTheDocument();
    expect(screen.queryByTestId("truthfulness-related-skills[0]")).not.toBeInTheDocument();
  });

  it("without an atsReport prop at all, behaviour is unchanged (back-compat)", () => {
    render(withIntl(<TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} />));
    expect(screen.getByTestId("truthfulness-flag-skills[0]")).toBeInTheDocument();
  });

  it("grounded claims are never affected by the ledger join", () => {
    render(
      withIntl(
        <TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} atsReport={ATS_WITH_CLAIMABLE_MATCH} />,
      ),
    );
    expect(screen.queryByTestId("truthfulness-related-work_history[0].bullets[0]")).not.toBeInTheDocument();
    expect(screen.queryByTestId("truthfulness-flag-work_history[0].bullets[0]")).not.toBeInTheDocument();
  });

  it("case-folded match still joins (e.g. ledger concept differs only in case)", () => {
    const atsReport: ATSReport = {
      checks: [],
      keywords: { present: ["strategic planning"], missing: [], claimable_concepts: ["strategic planning"] },
    };
    render(withIntl(<TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} atsReport={atsReport} />));
    expect(screen.getByTestId("truthfulness-related-skills[0]")).toBeInTheDocument();
  });

  it("DE locale: related-evidence chip and note render in German", () => {
    render(
      withIntl(
        <TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} atsReport={ATS_WITH_CLAIMABLE_MATCH} />,
        "de",
      ),
    );
    expect(screen.getByTestId("truthfulness-chip-related").textContent).toBe("Verwandter Beleg");
    expect(screen.getByTestId("truthfulness-related-note").textContent).toContain("verwandte Belege");
  });

  it("drawer shows the related-evidence claim with the neutral chip and honest detail text", () => {
    render(
      withIntl(
        <TruthfulnessPanel report={UNBACKED_WITH_LEDGER_MATCH_REPORT} atsReport={ATS_WITH_CLAIMABLE_MATCH} />,
      ),
    );
    fireEvent.click(screen.getByTestId("truthfulness-details-button"));
    const drawer = screen.getByTestId("truthfulness-drawer");
    expect(within(drawer).getByTestId("truthfulness-drawer-claim-skills[0]")).toBeInTheDocument();
    expect(
      within(drawer).getByText("Related evidence — not literally in your records."),
    ).toBeInTheDocument();
  });
});

// #249/US266 letter panel louder-failure copy: a sibling backend change adds
// `unverifiable_dominated: bool` to the report itself (>50% unverifiable) —
// distinct from the existing FRONTEND heuristic above (isUnverifiableDominant,
// #237/F14). Defensive: the field may be absent on older reports (=> false).
const BACKEND_DOMINATED_REPORT: TruthfulnessReport = {
  version: "1.2",
  document_kind: "cover_letter",
  claims: [
    {
      claim: { text: "Excited to bring my skills to your team.", location: "body.paragraphs[0][0]", kind: "sentence" },
      verdict: { verdict: "unverifiable", checker: "grounding", evidence: [], detail: null },
    },
  ],
  counts: { grounded: 0, inflated: 0, unbacked: 0, unverifiable: 1 },
  stated_limit: "This report verifies document-vault consistency only.",
  unverifiable_dominated: true,
};

describe("TruthfulnessPanel — unverifiable_dominated backend field (louder letter warning)", () => {
  it("renders a loud warning banner when the backend field is true", () => {
    render(withIntl(<TruthfulnessPanel report={BACKEND_DOMINATED_REPORT} />));
    const banner = screen.getByTestId("truthfulness-unverifiable-dominated-warning");
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain("unreviewed");
  });

  it("does not render the banner when the field is absent (older reports, back-compat)", () => {
    render(withIntl(<TruthfulnessPanel report={UNVERIFIABLE_DOMINATED_REPORT} />));
    expect(
      screen.queryByTestId("truthfulness-unverifiable-dominated-warning"),
    ).not.toBeInTheDocument();
  });

  it("does not render the banner when the field is explicitly false", () => {
    render(
      withIntl(
        <TruthfulnessPanel report={{ ...BACKEND_DOMINATED_REPORT, unverifiable_dominated: false }} />,
      ),
    );
    expect(
      screen.queryByTestId("truthfulness-unverifiable-dominated-warning"),
    ).not.toBeInTheDocument();
  });

  it("DE locale: the warning banner renders German copy", () => {
    render(withIntl(<TruthfulnessPanel report={BACKEND_DOMINATED_REPORT} />, "de"));
    const banner = screen.getByTestId("truthfulness-unverifiable-dominated-warning");
    expect(banner.textContent).toContain("ungeprüft");
  });
});

describe("TruthfulnessPanel", () => {
  it("unverifiable-dominated report (F14): headline is NOT the green all-clear state", () => {
    render(withIntl(<TruthfulnessPanel report={UNVERIFIABLE_DOMINATED_REPORT} />));
    const status = screen.getByTestId("truthfulness-status");
    expect(status.textContent).not.toContain("everything backed");
    expect(status.className).not.toContain("text-on-surface");
    expect(status.className).toContain("text-warning");
  });

  it("grounded-dominated report stays the green all-clear state (unchanged)", () => {
    render(withIntl(<TruthfulnessPanel report={ALL_GREEN_REPORT} />));
    const status = screen.getByTestId("truthfulness-status");
    expect(status.textContent).toContain("everything backed");
  });


  it("misattributed claims are loud red flags on the compact card (Oracle v2, #196)", () => {
    render(withIntl(<TruthfulnessPanel report={MISATTRIBUTED_REPORT} />));
    // counts as "needs review", not all-clear
    expect(screen.getByTestId("truthfulness-status").textContent).toContain("1 claim");
    expect(
      screen.getByTestId("truthfulness-flag-work_history[0].bullets[0]"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("truthfulness-chip-misattributed").textContent).toBe(
      "Misattributed",
    );
  });

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
