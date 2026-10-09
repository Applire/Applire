// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * Strawberry build 2 adversarial review — #702 / #737 frontend findings.
 *
 * Every test here is EXPECTED TO FAIL on the reviewed tree (`a96007bb`); each
 * one pins one finding of
 * `Documents/Runs/Strawberry/build-2/adv-review/findings.md`.
 * The item key is the backend's REAL shape: `critic:<_norm_quote(letter
 * sentence)>` — the whole folded sentence (R's and E's fixtures shorten it to
 * `critic:hygiene`, which hides finding 9).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { CrossDocumentSection } from "../CrossDocumentSection";
import { CoverLetterContentTab } from "@/components/cover-letter/CoverLetterContentTab";
import { buildCrossDocument } from "@/lib/review-groups";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";

const TRANSFER =
  "Hygiene- und Dokumentationsdisziplin aus Kosmetik-Verpackungen, einem Sauberraumbereich seit 2021 und zehn Jahren ISO-9001-Audit-Praxis sowie neun Jahre Kunststofftechnik mit Spritzguss und Montage sind jedoch übertragbare Grundlagen.";
// What `group_cross_document` emits for this sentence (backend replay of the
// 2026-09-13 Marcus critic report): the folded sentence IS the key.
const REAL_KEY =
  "critic:hygiene und dokumentationsdisziplin aus kosmetik verpackungen, einem sauberraumbereich seit 2021 und zehn jahren iso 9001 audit praxis sowie neun jahre kunststofftechnik mit spritzguss und montage sind jedoch übertragbare grundlagen.";

const REPORT: OutcomeCriticReport = {
  ran: true,
  mount: "letter",
  dropped_citations: 0,
  advisories: [],
  cross_document: [
    {
      key: REAL_KEY,
      letter_state: TRANSFER,
      concepts: ["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "ISO-9001-Audit-Praxis"],
      letter_only: ["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "ISO-9001-Audit-Praxis"],
      kinds: ["letter_only"],
      weight: "high",
      figures: ["zehn Jahren", "2021"],
    },
  ],
};

function renderSection(criticReport: OutcomeCriticReport) {
  render(
    withIntl(
      <CrossDocumentSection
        documentKind="cover-letter"
        documentId="cl-1"
        criticReport={criticReport}
        reviewState={null}
        cvEditHref="/flow/f1/cv?tab=edit"
        onRefresh={vi.fn()}
      />,
      "de",
    ),
  );
}

describe("adv-review b2 — cross-document card and Edit tab", () => {
  afterEach(() => vi.restoreAllMocks());

  // 9 — `xdoc` carries the letter sentence in the URL.
  it("test_adv_review_9 add-to-CV link carries no letter text in the URL", () => {
    renderSection(REPORT);
    const href = decodeURIComponent(screen.getByTestId("review-xdoc-add-to-cv").getAttribute("href") ?? "");
    // E's own claim (cv/page.tsx): "no document text travels in the URL".
    expect(href).not.toMatch(/kosmetik|sauberraumbereich|audit praxis/i);
  });

  // 10 — a critic whose judgement call errored reads as "nothing to cross-read".
  it("test_adv_review_10 a critic judgement_error is stated as not checked, never silent", () => {
    const errored: OutcomeCriticReport = {
      ran: true,
      reason: "judgement_error",
      mount: "letter",
      dropped_citations: 0,
      advisories: [],
      cross_document: [],
    };
    expect(buildCrossDocument(errored, null).unknown).toBe(true);
    renderSection(errored);
    expect(screen.getByTestId("review-xdoc-unknown")).toBeInTheDocument();
  });

  // 11 — the letter body has no unload guard (the CV section editor has one).
  it("test_adv_review_11 an unsaved letter body asks before the page unloads", () => {
    render(
      withIntl(
        <CoverLetterContentTab
          coverLetterId="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
          letterData={{ body: { paragraphs: ["Erster Absatz.", "Zweiter Absatz."] } }}
          initialBody={"Erster Absatz.\n\nZweiter Absatz."}
          onSectionSaved={vi.fn()}
          onDirtyChange={vi.fn()}
        />,
      ),
    );
    fireEvent.change(screen.getByTestId("cl-body-textarea"), { target: { value: "Mein neuer Text, nicht gespeichert." } });
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });
});
