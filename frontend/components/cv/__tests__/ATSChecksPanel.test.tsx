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

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import ATSChecksPanel, { type ATSReport } from "../ATSChecksPanel";

const REPORT_WITH_FAILURES: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "contact-phone", status: "fail", details: "No phone found" },
    { id: "work-1", status: "pass" },
    { id: "reading-order", status: "fail", details: null },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript", "React"],
    missing: ["Kubernetes", "Docker"],
  },
};

// US203: the report splits missing keywords into claimable (held but absent — fixable)
// vs honest-gap (not in the profile). The two buckets must render distinctly.
const REPORT_WITH_BUCKETS: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript"],
    missing: ["React", "Kubernetes"],
    missing_claimable: ["React"],
    missing_honest_gap: ["Kubernetes"],
  },
};

// #234 (Tiramisu founder-acceptance F1): every structural check passes, but the
// Keyword Ledger guard could not surface one supported keyword anywhere in the
// document — the headline must not read as a plain green all-clear.
const REPORT_WITH_BUCKETS_ALL_PASS: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript"],
    missing: ["React"],
    missing_claimable: ["React"],
    missing_honest_gap: [],
  },
};

const REPORT_WITH_UNSUPPORTED: ATSReport = {
  checks: [{ id: "contact-name", status: "pass" }],
  keywords: {
    present: ["TypeScript", "DevSecOps"],
    missing: [],
    missing_claimable: [],
    missing_honest_gap: [],
    present_unsupported: ["DevSecOps"],
  },
};

// E042/US239 (ADR-051): a page-length check can PASS with a non-null `details`
// string (advisory states — "meets your chosen target" / "acceptable for senior
// profiles"). The trap: the panel only rendered `details` for FAILING checks —
// this report exercises the advisory-pass path, mirroring the real backend shape.
// E042 follow-up (ADR-038): the backend now also sends details_key/details_params
// so the panel can localise the advisory; `details` stays the EN fallback.
const REPORT_WITH_ADVISORY: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    {
      id: "page-length",
      status: "pass",
      details: "3 pages — meets your chosen target of 3; the DACH norm is 2 pages",
      details_key: "page-length-target",
      details_params: { pages: 3, target: 3, region: "DACH", standard: 2 },
    },
  ],
  keywords: {
    present: ["TypeScript"],
    missing: [],
  },
};

// A legacy persisted report (pre-details_key JSONB) — the panel must fall back to
// the raw EN `details` string instead of rendering nothing or a raw key path.
const REPORT_LEGACY_ADVISORY: ATSReport = {
  checks: [
    {
      id: "page-length",
      status: "pass",
      details: "3 pages — acceptable for senior profiles; the DACH norm is 2 pages",
    },
  ],
  keywords: { present: [], missing: [] },
};

const REPORT_ALL_PASS: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "work-0", status: "pass" },
    { id: "work-1", status: "pass" },
    { id: "skills", status: "pass" },
  ],
  keywords: {
    present: ["TypeScript", "React", "Node.js"],
    missing: [],
  },
};

// E056/ADR-077 clauses 3+5: per-pin presence measurement — present, unmet
// (present=false), stale, and truth-floor-removed all render distinctly.
const REPORT_WITH_PINNED_FACTS: ATSReport = {
  checks: [{ id: "contact-name", status: "pass" }],
  keywords: { present: [], missing: [] },
  pinned_facts: [
    { pin_id: "p1", entry_type: "work", quote: "Led a team of 8 engineers", present: true, stale: false },
    { pin_id: "p2", entry_type: "skill", quote: "Kubernetes", present: false, stale: false },
    { pin_id: "p3", entry_type: "certification", quote: "PMP", present: false, stale: true },
    {
      pin_id: "p4",
      entry_type: "signature_story",
      quote: "Cut deployment time by 90%",
      present: false,
      stale: false,
      removed_by_truth_floor: true,
    },
  ],
};

// #238 page-length driver (ADR-077 clause 5): a failing page-length check
// carrying a structured driver — N present pinned facts occupy space the
// condense loop was forbidden to reclaim.
const REPORT_PAGE_LENGTH_WITH_PIN_DRIVER: ATSReport = {
  checks: [
    {
      id: "page-length",
      status: "fail",
      details: "3 pages — exceeds the DACH norm of 2 pages (max 2)",
      details_key: "page-length-exceeds",
      details_params: { pages: 3, region: "DACH", standard: 2, max: 2 },
      driver: { pinned_facts: 2 },
    },
  ],
  keywords: { present: [], missing: [] },
};

// E057/ADR-079 clause 4 groundwork (#629, story #637): a THIRD ATSCheck
// status, not_applicable, for a check that genuinely cannot be evaluated on
// the artefact (e.g. the page-length band on a .docx export, which has no
// fixed pagination until a renderer lays it out). No producer constructs one
// in a real report yet — these are synthetic fixtures, same as every other
// report constant in this file.
const REPORT_WITH_NOT_APPLICABLE: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    {
      id: "page-length",
      status: "not_applicable",
      details: "page count is not defined for this export format",
    },
  ],
  keywords: { present: ["TypeScript"], missing: [] },
};

// A group with a base id shared by a pass and a not_applicable check — the
// drawer's "X of Y" denominator must exclude the not_applicable one.
const REPORT_WITH_MIXED_GROUP: ATSReport = {
  checks: [
    { id: "work-0", status: "pass" },
    { id: "work-1", status: "not_applicable", details: "not evaluable for this export format" },
  ],
  keywords: { present: [], missing: [] },
};

describe("ATSChecksPanel", () => {
  // Case 1: failed checks render inline on the compact card — visible without any interaction
  it("renders failed checks inline with data-testid ats-check-<id> and details", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const phoneCheck = screen.getByTestId("ats-check-contact-phone");
    expect(phoneCheck).toBeInTheDocument();
    expect(phoneCheck.textContent).toContain("No phone found");

    // Failed check without details must still render
    expect(screen.getByTestId("ats-check-reading-order")).toBeInTheDocument();

    // Only the FAILED checks render inline — passing checks live in the drawer
    expect(screen.getAllByTestId(/^ats-check-/)).toHaveLength(2);
  });

  // Case 2: all-pass report renders the compact happy path — no inline check rows
  it("renders no inline check rows when every check passes", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryAllByTestId(/^ats-check-/)).toHaveLength(0);
    expect(screen.getByTestId("ats-structure-status")).toBeInTheDocument();
  });

  // Case 3: missing keywords listed on the card
  it("lists missing keywords by name in ats-keywords-missing", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const missingEl = screen.getByTestId("ats-keywords-missing");
    expect(missingEl.textContent).toContain("Kubernetes");
    expect(missingEl.textContent).toContain("Docker");
  });

  // Case 4: keyword coverage ring shows X/Y counts — never a percentage
  it("renders keyword coverage as X of Y counts in ats-keywords-coverage", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    const coverageEl = screen.getByTestId("ats-keywords-coverage");
    // present=2, total=4
    expect(coverageEl.textContent).toMatch(/2/);
    expect(coverageEl.textContent).toMatch(/4/);
    expect(coverageEl.textContent).not.toContain("%");
  });

  // Case 5: report === null → neutral unavailable state, no crash
  it("renders ats-unavailable when report is null", () => {
    render(withIntl(<ATSChecksPanel report={null} />));
    expect(screen.getByTestId("ats-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("ats-panel")).toBeNull();
  });

  // Case 6: no aggregate score — no "%" character anywhere, even with the drawer open
  it("renders no percentage/aggregate score anywhere", () => {
    const { container } = render(
      withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />)
    );
    fireEvent.click(screen.getByTestId("ats-details-button"));
    expect(container.textContent).not.toContain("%");
  });

  // Case 7: details button opens the drawer with the full grouped checks list
  it("opens the drawer with grouped checks via the details button", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryByTestId("ats-drawer")).toBeNull();

    fireEvent.click(screen.getByTestId("ats-details-button"));
    expect(screen.getByTestId("ats-drawer")).toBeInTheDocument();

    // work-0 + work-1 collapse into ONE grouped row with a (2 of 2) count
    const workGroup = screen.getByTestId("ats-drawer-check-work");
    expect(workGroup.textContent).toMatch(/2/);
    // Single-instance checks render one row each
    expect(screen.getByTestId("ats-drawer-check-contact-name")).toBeInTheDocument();
    expect(screen.getByTestId("ats-drawer-check-skills")).toBeInTheDocument();
    // Keyword section is present in the drawer
    expect(screen.getByTestId("ats-drawer-coverage")).toBeInTheDocument();
  });

  // Case 8: drawer closes via the close button
  it("closes the drawer via the close button", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    fireEvent.click(screen.getByTestId("ats-details-button"));
    expect(screen.getByTestId("ats-drawer")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("ats-drawer-close"));
    expect(screen.queryByTestId("ats-drawer")).toBeNull();
  });

  // Case 9: failed checks surface in the drawer's grouped rows with details
  it("shows failure details inside the drawer groups", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    fireEvent.click(screen.getByTestId("ats-details-button"));
    const phoneGroup = screen.getByTestId("ats-drawer-check-contact-phone");
    expect(phoneGroup.textContent).toContain("No phone found");
  });

  // Bonus: all-pass report shows no missing keywords line
  it("does not render ats-keywords-missing when there are no missing keywords", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    expect(screen.queryByTestId("ats-keywords-missing")).toBeNull();
  });

  // #234 (Tiramisu founder-acceptance F1): a document that is structurally sound
  // (every ATSCheck passes) but is still missing supported keywords must NOT ship
  // under the plain green "all checks passed" headline — that reads as an all-clear
  // when the guard couldn't surface everything the vault supports.
  it("shows a distinct non-green headline when checks pass but claimable keywords are missing", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_BUCKETS_ALL_PASS} />));
    const status = screen.getByTestId("ats-structure-status");
    // The plain green all-clear text must NOT appear.
    expect(status.textContent).not.toContain("all checks passed");
    // A distinct message naming the still-missing count must appear instead.
    expect(status.textContent).toContain("1");
    // The headline glyph must not read as a green checkmark.
    expect(status.querySelector(".text-success")).toBeNull();
  });

  it("keeps the plain green all-clear when checks pass and nothing claimable is missing", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
    const status = screen.getByTestId("ats-structure-status");
    expect(status.textContent).toContain("all checks passed");
    expect(status.querySelector(".text-success")).not.toBeNull();
  });

  // US203: claimable vs honest-gap missing keywords render in two distinct buckets
  it("renders missing-claimable and missing-honest-gap as distinct buckets", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_BUCKETS} />));
    const claimable = screen.getByTestId("ats-keywords-missing-claimable");
    const honestGap = screen.getByTestId("ats-keywords-missing-honest-gap");
    expect(claimable.textContent).toContain("React");
    expect(claimable.textContent).not.toContain("Kubernetes");
    expect(honestGap.textContent).toContain("Kubernetes");
    expect(honestGap.textContent).not.toContain("React");
  });

  // US203: a bucket with no entries is not rendered
  it("omits a missing bucket that has no keywords", () => {
    const onlyHonest: ATSReport = {
      checks: [{ id: "skills", status: "pass" }],
      keywords: {
        present: [],
        missing: ["Kubernetes"],
        missing_claimable: [],
        missing_honest_gap: ["Kubernetes"],
      },
    };
    render(withIntl(<ATSChecksPanel report={onlyHonest} />));
    expect(screen.queryByTestId("ats-keywords-missing-claimable")).toBeNull();
    expect(screen.getByTestId("ats-keywords-missing-honest-gap")).toBeInTheDocument();
  });

  // US203: back-compat — a report with no bucket fields still renders without crashing
  it("renders legacy reports without missing buckets", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
    expect(screen.getByTestId("ats-keywords-missing")).toBeInTheDocument();
  });

  // #117 (ADR-048 fourth quadrant): a present keyword without profile backing is a
  // truthfulness warning, not silent ordinary coverage.
  it("renders present-but-unsupported keywords as a warning row", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_UNSUPPORTED} />));
    const row = screen.getByTestId("ats-keywords-present-unsupported");
    expect(row).toBeInTheDocument();
    expect(row.textContent).toContain("DevSecOps");
  });

  it("omits the unsupported row when empty or absent", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_BUCKETS} />));
    expect(screen.queryByTestId("ats-keywords-present-unsupported")).toBeNull();
  });

  // E042/US239: a passing check WITH details (page-length advisory) must surface
  // its details on the compact card, distinctly from a failure.
  it("renders a passing check's details as an advisory row on the compact card", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow).toBeInTheDocument();
    expect(advisoryRow.textContent).toContain("meets your chosen target of 3");
    // Never rendered as a failure row
    expect(screen.queryByTestId("ats-check-page-length")).toBeNull();
  });

  // A pass-with-advisory check must not flip the overall structure status to "issues"
  it("keeps structureOk when the only details-bearing check is a pass", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    expect(screen.getByTestId("ats-structure-status")).toBeInTheDocument();
    expect(screen.queryByText(/structureIssues/)).toBeNull();
  });

  // The drawer's grouped view must also surface the advisory detail, styled
  // distinctly (data-testid) from a failing check's detail.
  it("shows a pass-with-advisory detail inside the drawer group", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    fireEvent.click(screen.getByTestId("ats-details-button"));
    const advisory = screen.getByTestId("ats-drawer-advisory-page-length");
    expect(advisory.textContent).toContain("meets your chosen target of 3");
    // The group itself still reads as passing (no failing entries)
    const group = screen.getByTestId("ats-drawer-check-page-length");
    expect(group.textContent).not.toContain("✗");
  });

  // E042 follow-up (ADR-038): a details_key must render the LOCALISED string in
  // German chrome — the raw backend-English `details` was the bug.
  it("localises a keyed advisory detail in the de locale", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow.textContent).toContain(
      "3 Seiten — entspricht deinem gewählten Ziel von 3; die DACH-Norm sind 2 Seiten",
    );
    expect(advisoryRow.textContent).not.toContain("meets your chosen target");
  });

  // Back-compat: a legacy report without details_key still shows its EN details.
  it("falls back to the raw details string when no details_key is present", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_LEGACY_ADVISORY} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow.textContent).toContain("acceptable for senior profiles");
  });

  // Adversarial find (2026-07-16): a KNOWN key with null/absent details_params must
  // fall back to the EN details — not render the raw key path with an IntlError.
  it("falls back to raw details when details_params is null for a known key", () => {
    const report: ATSReport = {
      checks: [
        {
          id: "page-length",
          status: "pass",
          details: "3 pages — meets your chosen target of 3; the DACH norm is 2 pages",
          details_key: "page-length-target",
          details_params: null,
        },
      ],
      keywords: { present: [], missing: [] },
    };
    render(withIntl(<ATSChecksPanel report={report} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-page-length");
    expect(advisoryRow.textContent).toContain("meets your chosen target of 3");
    expect(advisoryRow.textContent).not.toContain("checkDetails");
  });

  // #238 (founder-acceptance F4): a chosen page target the condense loop could
  // not hit ships as a FAILING check (never a pass-with-advisory) — the miss
  // must read as a genuine problem, not senior-profile advice. It renders
  // through the existing failed-check path: no new panel state was added.
  const REPORT_WITH_TARGET_MISSED: ATSReport = {
    checks: [
      { id: "contact-name", status: "pass" },
      {
        id: "page-length",
        status: "fail",
        details:
          "3 pages — couldn't condense to your 2-page target without cutting relevant content; the DACH max is 3 pages",
        details_key: "page-length-target-missed",
        details_params: { pages: 3, target: 2, region: "DACH", standard: 2, max: 3 },
      },
    ],
    keywords: { present: ["TypeScript"], missing: [] },
  };

  it("renders a missed page-length target as an inline failure, not an advisory", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_TARGET_MISSED} />));
    const failRow = screen.getByTestId("ats-check-page-length");
    expect(failRow.textContent).toContain(
      "couldn't condense to your 2-page target without cutting relevant content",
    );
    expect(failRow.textContent).not.toContain("senior");
    // Never rendered as a pass-with-advisory row.
    expect(screen.queryByTestId("ats-advisory-page-length")).toBeNull();
    // The headline reads as a problem, not the all-clear state.
    expect(screen.getByTestId("ats-structure-status").textContent).not.toContain("✓");
  });

  it("localises the missed page-length target detail into German", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_TARGET_MISSED} />, "de"));
    const failRow = screen.getByTestId("ats-check-page-length");
    expect(failRow.textContent).toContain(
      "konnte ohne Kürzung relevanter Inhalte nicht auf dein Ziel von 2 Seiten gebracht werden",
    );
    expect(failRow.textContent).not.toContain("couldn't condense");
  });

  // #391 interim (PO-ruled 2026-08-15, ADR-076 amendment 4 point 6): a
  // measurement-only advisory — same pass-with-advisory shape as page-length,
  // never a failure (ADR-062 clause 5: visibility, never adjudication).
  const REPORT_WITH_WEAK_VAULT_TIE: ATSReport = {
    checks: [
      { id: "contact-name", status: "pass" },
      {
        id: "skills-weak-vault-tie",
        status: "pass",
        // The EN `details` fallback (rendered only pre-localisation / as the
        // legacy-report fallback) may carry English scaffold words — but
        // `details_params` must stay locale-neutral, same contract as the
        // page-length checks' numeric/region params (review finding: an
        // English-worded param leaked "shares only" into the German chip).
        details:
          "skill(s) tied to your profile by a single shared word only — worth a second look before sending: '5 Jahre Controlling-Erfahrung' (shares only 'Controlling')",
        details_key: "skills-weak-vault-tie",
        details_params: {
          skills: "'5 Jahre Controlling-Erfahrung' ('Controlling')",
          count: 1,
        },
      },
    ],
    keywords: { present: ["Controlling"], missing: [] },
  };

  it("renders the #391 weak-vault-tie advisory on the compact card, never as a failure", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_WEAK_VAULT_TIE} />));
    const advisoryRow = screen.getByTestId("ats-advisory-skills-weak-vault-tie");
    expect(advisoryRow.textContent).toContain("Controlling-Erfahrung");
    expect(screen.queryByTestId("ats-check-skills-weak-vault-tie")).toBeNull();
    expect(screen.getByTestId("ats-structure-status").textContent).toContain("✓");
  });

  it("localises the #391 weak-vault-tie advisory into German", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_WEAK_VAULT_TIE} />, "de"));
    const advisoryRow = screen.getByTestId("ats-advisory-skills-weak-vault-tie");
    expect(advisoryRow.textContent).toContain(
      "nur durch ein einzelnes gemeinsames Wort mit deinem Profil verknüpft",
    );
    expect(advisoryRow.textContent).not.toContain("tied to your profile only by");
    // Review finding: details_params.skills used to carry the EN scaffold
    // "(shares only '...')", leaking English into the German chip even
    // though the surrounding sentence was localised. Pin the absence.
    expect(advisoryRow.textContent).not.toContain("shares only");
  });

  it("stays silent when no skills-weak-vault-tie check is present", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_ADVISORY} />));
    expect(screen.queryByTestId("ats-advisory-skills-weak-vault-tie")).toBeNull();
  });

  // E056/ADR-077 — per-pin presence measurement.
  describe("pinned facts", () => {
    it("renders a row per pin with present/unmet, stale and truth-floor status", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_PINNED_FACTS} />));
      expect(screen.getByTestId("ats-pinned-facts")).toBeInTheDocument();

      // present=true: no "unmet" marker
      expect(screen.getByTestId("ats-pinned-fact-p1")).toBeInTheDocument();
      expect(screen.queryByTestId("ats-pinned-fact-unmet-p1")).toBeNull();

      // present=false: the unmet marker renders
      expect(screen.getByTestId("ats-pinned-fact-unmet-p2")).toBeInTheDocument();

      // stale
      expect(screen.getByTestId("ats-pinned-fact-stale-p3")).toBeInTheDocument();

      // removed_by_truth_floor (hierarchy: truth > pin, surfaced never silent)
      expect(screen.getByTestId("ats-pinned-fact-floor-p4")).toBeInTheDocument();
    });

    it("localises the pinned-facts section and statuses into German", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_PINNED_FACTS} />, "de"));
      expect(screen.getByTestId("ats-pinned-facts").textContent).toContain("Festgelegte Fakten");
      expect(screen.getByTestId("ats-pinned-fact-unmet-p2").textContent).toContain(
        "nicht im Dokument",
      );
      expect(screen.getByTestId("ats-pinned-fact-stale-p3").textContent).toContain("Veraltet");
      expect(screen.getByTestId("ats-pinned-fact-floor-p4").textContent).toContain(
        "durch Wahrheits-Prüfung entfernt",
      );
    });

    it("stays silent when the report carries no pin context (legacy reports)", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
      expect(screen.queryByTestId("ats-pinned-facts")).toBeNull();
    });

    it("renders the structured page-length driver line under the failing check", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_PAGE_LENGTH_WITH_PIN_DRIVER} />));
      const driverLine = screen.getByTestId("ats-check-page-length-pin-driver");
      expect(driverLine.textContent).toContain("2");
      expect(driverLine.textContent).toContain("pinned facts");
    });

    it("localises the page-length driver line into German (ICU plural)", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_PAGE_LENGTH_WITH_PIN_DRIVER} />, "de"));
      const driverLine = screen.getByTestId("ats-check-page-length-pin-driver");
      expect(driverLine.textContent).toContain("festgelegte Fakten");
    });

    it("does not render the driver line when the check carries no driver", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_FAILURES} />));
      expect(screen.queryByTestId("ats-check-contact-phone-pin-driver")).toBeNull();
    });

    // #580: ledger_conflict is a fact about the QUOTE (the job's do-not-claim
    // terms it carries), appended to the unmet marker so the report and the
    // pin control (PinnedFactsPanel) say the same thing.
    const REPORT_WITH_LEDGER_CONFLICT: ATSReport = {
      checks: [{ id: "contact-name", status: "pass" }],
      keywords: { present: [], missing: [] },
      pinned_facts: [
        {
          pin_id: "p5",
          entry_type: "skill",
          quote: "Kubernetes",
          present: false,
          stale: false,
          ledger_conflict: ["microservices"],
        },
      ],
    };

    it("appends the do-not-claim term to the unmet marker when ledger_conflict is present", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_LEDGER_CONFLICT} />));
      const unmet = screen.getByTestId("ats-pinned-fact-unmet-p5");
      expect(unmet.textContent).toContain("not in the document");
      expect(unmet.textContent).toContain("do-not-claim term: microservices");
    });

    it("localises the do-not-claim term into German", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_LEDGER_CONFLICT} />, "de"));
      const unmet = screen.getByTestId("ats-pinned-fact-unmet-p5");
      expect(unmet.textContent).toContain("Nicht-behaupten-Begriff: microservices");
    });

    it("omits the do-not-claim suffix when ledger_conflict is empty or absent", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_PINNED_FACTS} />));
      const unmet = screen.getByTestId("ats-pinned-fact-unmet-p2");
      expect(unmet.textContent).not.toContain("do-not-claim");
    });
  });

  // E057/ADR-079 clause 4 groundwork (#629, story #637): the third check
  // state must render distinguishably — not as a pass, not as a fail — at
  // every level the panel renders a status (compact card, drawer group, and
  // the group's "X of Y" count).
  describe("not_applicable status (E057/ADR-079 clause 4)", () => {
    it("renders a not_applicable check distinctly on the compact card — not pass, not fail", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_NOT_APPLICABLE} />));
      const naRow = screen.getByTestId("ats-notapplicable-page-length");
      expect(naRow).toBeInTheDocument();
      expect(naRow.textContent).toContain("page count is not defined for this export format");
      // Never rendered through the failure or pass-with-advisory paths.
      expect(screen.queryByTestId("ats-check-page-length")).toBeNull();
      expect(screen.queryByTestId("ats-advisory-page-length")).toBeNull();
      // Neutral styling — neither the success nor the critical palette.
      expect(naRow.querySelector(".text-success")).toBeNull();
      expect(naRow.querySelector(".text-critical")).toBeNull();
    });

    it("does not treat a not_applicable check as a structure issue", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_NOT_APPLICABLE} />));
      const status = screen.getByTestId("ats-structure-status");
      // Nothing FAILED — the headline stays the plain green all-clear.
      expect(status.textContent).toContain("all checks passed");
      expect(screen.queryByText(/structureIssues/)).toBeNull();
    });

    it("localises the not-applicable label into German", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_NOT_APPLICABLE} />, "de"));
      const naRow = screen.getByTestId("ats-notapplicable-page-length");
      expect(naRow.textContent).toContain("Nicht anwendbar");
    });

    // Regression test for the exact defect this task exists to prevent: the
    // drawer's group-level "all pass?" check used to be `passed ===
    // g.checks.length`, which reads a not_applicable check (neither "pass"
    // nor "fail") as a FAILURE — a single not_applicable check rendered a
    // red ✗ on a band that never failed anything.
    it("does not render a not_applicable-only drawer group as a failure (regression)", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_NOT_APPLICABLE} />));
      fireEvent.click(screen.getByTestId("ats-details-button"));
      const group = screen.getByTestId("ats-drawer-check-page-length");
      expect(group.textContent).not.toContain("✗");
      expect(group.querySelector(".text-critical")).toBeNull();
    });

    // The other half of the same property: a group with NOTHING gradable
    // (every check in it is not_applicable) must not render the green
    // checkmark either — that would read as "verified clean" for something
    // that was never evaluated at all, the exact #634 failure class.
    it("does not render a not_applicable-only drawer group as a pass either", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_NOT_APPLICABLE} />));
      fireEvent.click(screen.getByTestId("ats-details-button"));
      const group = screen.getByTestId("ats-drawer-check-page-length");
      expect(group.textContent).not.toContain("✓");
      expect(group.querySelector(".text-success")).toBeNull();
    });

    it("excludes a not_applicable check from the drawer group's pass/fail count, noting it separately", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_WITH_MIXED_GROUP} />));
      fireEvent.click(screen.getByTestId("ats-details-button"));
      const group = screen.getByTestId("ats-drawer-check-work");
      // Must never read "1 of 2 passed" — only one check in the group was
      // actually gradable, the other was not_applicable.
      expect(group.textContent).not.toMatch(/1 of 2|1 von 2/);
      // The not_applicable check is named in its own note, not silently
      // dropped — this is the "absent check reads as evidence" trap.
      const naNote = screen.getByTestId("ats-drawer-notapplicable-work-1");
      expect(naNote.textContent).toContain("not evaluable for this export format");
      // The group still reads as OK — nothing in it failed.
      expect(group.textContent).not.toContain("✗");
    });

    it("stays silent when no check is not_applicable", () => {
      render(withIntl(<ATSChecksPanel report={REPORT_ALL_PASS} />));
      expect(screen.queryAllByTestId(/^ats-notapplicable-/)).toHaveLength(0);
    });
  });
});

// ADR-079 cl. 4 / ADR-039 amendment 2026-09-01: the .docx export's page band is
// `not_applicable` and carries a details_key with an EMPTY BUT PRESENT
// details_params. That combination is the trap: `detailText` localises only when
// details_params is truthy — deliberately, because next-intl renders the raw key
// path instead of throwing on a missing ICU variable, so a keyed check whose
// params are absent (a partially-migrated persisted report) must take the EN
// fallback. `{}` is truthy in JS, so it means "params measured, and there are
// none" rather than "params missing", and the German user gets German. Without
// this test, shipping `details_params=None` from the backend would silently
// serve the English fallback to a DE user and every other gate would stay green.
describe("ATSChecksPanel — not_applicable page band (ADR-079 cl. 4)", () => {
  const REPORT_WITH_NA_BAND: ATSReport = {
    checks: [
      { id: "contact-name", status: "pass" },
      { id: "skills", status: "pass" },
      {
        id: "page-length",
        status: "not_applicable",
        details:
          "not applicable — this document has no fixed pagination until a word processor lays it out",
        details_key: "page-length-not-applicable",
        details_params: {},
      },
    ],
    keywords: { present: [], missing: [] },
  };

  it("renders the localized German reason, not the English fallback", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_NA_BAND} />, "de"));

    expect(
      screen.getByText(/keine feste Seitenaufteilung/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/no fixed pagination/i),
    ).not.toBeInTheDocument();
  });

  it("does not mark the band as a failure", () => {
    render(withIntl(<ATSChecksPanel report={REPORT_WITH_NA_BAND} />, "de"));

    // The whole point: a lone not_applicable check in its own group used to
    // make `passed === checks.length` false and paint a red ✗ on a band that
    // is not a failure.
    expect(screen.queryByText("✗Seitenlänge")).not.toBeInTheDocument();
  });
});
