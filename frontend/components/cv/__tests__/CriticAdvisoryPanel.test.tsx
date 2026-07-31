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

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import CriticAdvisoryPanel, { type OutcomeCriticReport } from "../CriticAdvisoryPanel";

// ADR-060 (advisory-only, never gates delivery) — Pass B (letter mount)
// shape: a letter-only concept that never appears on the CV.
const REPORT_LETTER_ONLY: OutcomeCriticReport = {
  ran: true,
  reason: null,
  mount: "letter",
  advisories: [
    {
      concept: "team leadership",
      kind: "letter_only",
      cv_state: null,
      cv_detail: null,
      letter_state: "led a cross-functional team of eight engineers",
      changed: false,
      message: {
        de: "Ihr Anschreiben erwähnt Teamführung, Ihr Lebenslauf nicht — prüfen Sie, ob das so gewollt ist.",
        en: "Your letter mentions team leadership, your CV doesn't — worth checking this is intentional.",
      },
    },
  ],
  dropped_citations: 0,
};

// Pass A (CV mount) internal-inconsistency shape: two spans on the same
// document, no letter_state at all.
const REPORT_CV_INTERNAL: OutcomeCriticReport = {
  ran: true,
  reason: null,
  mount: "cv",
  advisories: [
    {
      concept: "migration scope",
      kind: "internal_inconsistency",
      cv_state: "led the full-company cloud migration",
      cv_detail: "migrated the billing service to the new cloud platform",
      letter_state: null,
      changed: false,
      message: {
        de: "Ihre Zusammenfassung ist breiter formuliert als der zugehörige Punkt.",
        en: "Your summary claims a broader scope than the detail it rests on.",
      },
    },
  ],
  dropped_citations: 0,
};

const REPORT_MULTI: OutcomeCriticReport = {
  ran: true,
  reason: null,
  mount: "letter",
  advisories: [
    REPORT_LETTER_ONLY.advisories[0],
    {
      concept: "budget managed",
      kind: "numeric_inconsistency",
      cv_state: "managed a budget of €2M",
      cv_detail: null,
      letter_state: "managed a budget of €2.5M",
      changed: false,
      message: {
        de: "Die genannten Budgetzahlen weichen zwischen den Dokumenten ab.",
        en: "The budget figures differ between the two documents.",
      },
    },
  ],
  dropped_citations: 0,
};

describe("CriticAdvisoryPanel", () => {
  it("renders nothing when report is null", () => {
    const { container } = render(withIntl(<CriticAdvisoryPanel report={null} />));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("critic-advisory-panel")).toBeNull();
  });

  it("renders nothing when the critic did not run", () => {
    const report: OutcomeCriticReport = {
      ran: false,
      reason: "disabled",
      mount: null,
      advisories: [],
      dropped_citations: 0,
    };
    const { container } = render(withIntl(<CriticAdvisoryPanel report={report} />));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the critic ran but found no advisories", () => {
    const report: OutcomeCriticReport = {
      ran: true,
      reason: null,
      mount: "cv",
      advisories: [],
      dropped_citations: 0,
    };
    const { container } = render(withIntl(<CriticAdvisoryPanel report={report} />));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the localized message in English", () => {
    render(withIntl(<CriticAdvisoryPanel report={REPORT_LETTER_ONLY} />, "en"));
    expect(screen.getByTestId("critic-advisory-panel")).toBeInTheDocument();
    expect(screen.getByTestId("critic-advisory-message-0").textContent).toContain(
      "Your letter mentions team leadership",
    );
  });

  it("renders the localized message in German", () => {
    render(withIntl(<CriticAdvisoryPanel report={REPORT_LETTER_ONLY} />, "de"));
    expect(screen.getByTestId("critic-advisory-message-0").textContent).toContain(
      "Ihr Anschreiben erwähnt Teamführung",
    );
    expect(screen.getByTestId("critic-advisory-message-0").textContent).not.toContain(
      "Your letter mentions",
    );
  });

  it("shows the quoted letter span for a letter-only advisory, no CV quote", () => {
    render(withIntl(<CriticAdvisoryPanel report={REPORT_LETTER_ONLY} />));
    const letterQuote = screen.getByTestId("critic-advisory-letter-quote-0");
    expect(letterQuote.textContent).toContain("led a cross-functional team of eight engineers");
    expect(screen.queryByTestId("critic-advisory-cv-quote-0")).toBeNull();
  });

  it("shows both quoted CV spans for an internal-inconsistency advisory, no letter quote", () => {
    render(withIntl(<CriticAdvisoryPanel report={REPORT_CV_INTERNAL} />));
    const cvQuote = screen.getByTestId("critic-advisory-cv-quote-0");
    const cvDetailQuote = screen.getByTestId("critic-advisory-cv-detail-quote-0");
    expect(cvQuote.textContent).toContain("led the full-company cloud migration");
    expect(cvDetailQuote.textContent).toContain("migrated the billing service");
    expect(screen.queryByTestId("critic-advisory-letter-quote-0")).toBeNull();
  });

  it("renders one card per advisory when the report carries several", () => {
    render(withIntl(<CriticAdvisoryPanel report={REPORT_MULTI} />));
    expect(screen.getByTestId("critic-advisory-item-0")).toBeInTheDocument();
    expect(screen.getByTestId("critic-advisory-item-1")).toBeInTheDocument();
    expect(screen.getByTestId("critic-advisory-cv-quote-1").textContent).toContain(
      "managed a budget of €2M",
    );
    expect(screen.getByTestId("critic-advisory-letter-quote-1").textContent).toContain(
      "managed a budget of €2.5M",
    );
  });

  // The frame itself must never contradict the "nothing changed, your choice"
  // framing carried in the message bodies — the panel chrome states it too.
  it("renders the subtitle reinforcing that nothing was changed", () => {
    render(withIntl(<CriticAdvisoryPanel report={REPORT_LETTER_ONLY} />, "en"));
    expect(screen.getByTestId("critic-advisory-subtitle").textContent).toMatch(
      /nothing.*changed/i,
    );
  });
});
