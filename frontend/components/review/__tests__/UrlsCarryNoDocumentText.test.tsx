// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * Adversarial finding 9 (Strawberry build 2) — no URL built by the review
 * surface (R) or the Edit tab (E) may carry document text. URLs land in
 * browser history, access logs and the Referer.
 *
 * Built on the backend's REAL key shape: `critic:<the whole folded letter
 * sentence>` — a shortened fixture key (`critic:hygiene`) is what hid the
 * defect. Every `a[href]` the components render is checked against every
 * word of the letter sentence and its concepts.
 */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { CrossDocumentSection, crossDocumentHandle } from "../CrossDocumentSection";
import { LetterLookLine } from "@/components/document/LetterLookLine";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import type { ReviewState } from "@/lib/api/document-review";

const SENTENCE =
  "Hygiene- und Dokumentationsdisziplin aus Kosmetik-Verpackungen, einem Sauberraumbereich seit 2021 und zehn Jahren ISO-9001-Audit-Praxis sowie neun Jahre Kunststofftechnik mit Spritzguss und Montage sind jedoch übertragbare Grundlagen.";
const KEY =
  "critic:hygiene und dokumentationsdisziplin aus kosmetik verpackungen, einem sauberraumbereich seit 2021 und zehn jahren iso 9001 audit praxis sowie neun jahre kunststofftechnik mit spritzguss und montage sind jedoch übertragbare grundlagen.";
const CONCEPTS = ["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "ISO-9001-Audit-Praxis"];

const REPORT: OutcomeCriticReport = {
  ran: true,
  mount: "letter",
  dropped_citations: 0,
  advisories: [],
  cross_document: [
    { key: KEY, letter_state: SENTENCE, concepts: CONCEPTS, letter_only: CONCEPTS, kinds: ["letter_only"], weight: "high", figures: ["2021"] },
  ],
};

/** Every word of 4+ letters in the document text, lower-cased. */
const WORDS = Array.from(new Set(`${SENTENCE} ${CONCEPTS.join(" ")}`.toLowerCase().match(/[a-zäöüß]{4,}/g) ?? []));
// Path words that legitimately appear in a route.
const ROUTE_WORDS = new Set(["flow", "edit", "cover", "letter"]);

function hrefsIn(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("a[href]")).map((a) => decodeURIComponent(a.getAttribute("href") ?? ""));
}

function assertNoDocumentText(hrefs: string[]) {
  expect(hrefs.length).toBeGreaterThan(0);
  for (const href of hrefs) {
    const lower = href.toLowerCase();
    for (const w of WORDS) {
      if (ROUTE_WORDS.has(w)) continue;
      expect(lower, `"${w}" from the letter appears in ${href}`).not.toContain(w);
    }
    // A route plus a few short opaque params — never a sentence.
    expect(href.length).toBeLessThan(80);
  }
}

describe("finding 9 — URLs carry no document text", () => {
  for (const [label, state] of [
    ["open", null],
    ["kept", { walked_at: null, decisions: [{ finding_key: KEY, label: "x", action: "kept", at: "2026-10-08T00:00:00Z", undo: null }] }],
  ] as const) {
    it(`CrossDocumentSection (${label}) builds only opaque links`, () => {
      const { container } = render(
        withIntl(
          <CrossDocumentSection
            documentKind="cover-letter"
            documentId="cl-1"
            criticReport={REPORT}
            reviewState={state as ReviewState | null}
            cvEditHref="/flow/f1/cv?tab=edit"
            onRefresh={vi.fn()}
          />,
          "de",
        ),
      );
      if (label === "open") assertNoDocumentText(hrefsIn(container));
      else for (const h of hrefsIn(container)) expect(h.length).toBeLessThan(80);
    });
  }

  it("LetterLookLine links to the CV edit tab without content", () => {
    const { container } = render(withIntl(<LetterLookLine template="classic_german" cvEditHref="/flow/f1/cv?tab=edit" />));
    assertNoDocumentText(hrefsIn(container));
  });

  it("the handle is 8 hex characters, stable, and differs per key", () => {
    const h = crossDocumentHandle(KEY);
    expect(h).toMatch(/^[0-9a-f]{8}$/);
    expect(crossDocumentHandle(KEY)).toBe(h);
    expect(crossDocumentHandle(`${KEY} `)).not.toBe(h);
  });
});
