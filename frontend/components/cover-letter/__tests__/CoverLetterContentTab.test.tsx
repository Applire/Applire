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

/**
 * ADR-090 cl. 5 — *Let me edit it* on a review finding opens the letter's
 * body editor (its only editable section), nothing pre-filled. `openBodyNonce`
 * is a counter rather than a boolean so the SAME finding can be reopened after
 * the user closed it, while an unchanged value must not reopen it behind
 * their back.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { CoverLetterContentTab } from "../CoverLetterContentTab";
import { effectiveLetterBody, overrideParagraphs } from "@/lib/letter-body";

const LETTER_DATA = {
  header: { name: "Marcus Weber", email: "marcus@example.com" },
  recipient: { name: "Jane Recruiter", company: "Acme GmbH" },
  body: { paragraphs: ["First paragraph.", "Second paragraph."] },
  signature: { closing: "Kind regards", name: "Marcus Weber" },
};

function renderTab(overrides: Partial<React.ComponentProps<typeof CoverLetterContentTab>> = {}) {
  const props = {
    coverLetterId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    letterData: LETTER_DATA,
    onSectionSaved: vi.fn(),
    ...overrides,
  };
  const result = render(withIntl(<CoverLetterContentTab {...props} />));
  return {
    ...result,
    rerender: (next: Partial<React.ComponentProps<typeof CoverLetterContentTab>>) =>
      result.rerender(withIntl(<CoverLetterContentTab {...props} {...next} />)),
  };
}

describe("CoverLetterContentTab — openBodyNonce (ADR-090 cl. 5)", () => {
  afterEach(() => vi.clearAllMocks());

  it("does not open the body editor without a nonce", () => {
    renderTab();
    expect(screen.queryByTestId("cl-save-body-btn")).toBeNull();
  });

  it("opens the body editor when a nonce is supplied", () => {
    renderTab({ openBodyNonce: 1 });
    expect(screen.getByTestId("cl-save-body-btn")).toBeTruthy();
  });

  it("the same nonce does not reopen the editor after the user closed it", () => {
    const { rerender } = renderTab({ openBodyNonce: 1 });
    expect(screen.getByTestId("cl-save-body-btn")).toBeTruthy();
    // Close via Cancel — this only flips local `bodyEditing`, it does not
    // touch the consumed-nonce ref.
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByTestId("cl-save-body-btn")).toBeNull();
    // Re-render with the SAME nonce value (1): the effect's dependency is
    // unchanged, so it must not fire again and reopen the editor.
    rerender({ openBodyNonce: 1 });
    expect(screen.queryByTestId("cl-save-body-btn")).toBeNull();
  });

  it("a new nonce reopens the editor", () => {
    const { rerender } = renderTab({ openBodyNonce: 1 });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByTestId("cl-save-body-btn")).toBeNull();
    rerender({ openBodyNonce: 2 });
    expect(screen.getByTestId("cl-save-body-btn")).toBeTruthy();
  });
});

/**
 * D-2 (ADR-090 delivery run, HIGH): take-out → *Let me edit it* → save must
 * never write the pre-take-out text back. The page feeds the editor the
 * EFFECTIVE body (`effectiveLetterBody`: the saved override, split as the
 * backend renders it); the raw `letter_data` still holds the removed wording.
 */
describe("CoverLetterContentTab — starts from the effective body (D-2)", () => {
  afterEach(() => vi.restoreAllMocks());

  const RAW = {
    ...LETTER_DATA,
    body: { paragraphs: ["I built European e-commerce platforms.", "I led settlement for merchants."] },
  };
  // What the take-out saved (CRLF, as a browser textarea may send it).
  const OVERRIDES = { body: "I built platforms.\r\n\r\nI led settlement for merchants." };

  it("take-out → edit → save writes the taken-out text, never the removed wording", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    global.fetch = fetchMock as unknown as typeof fetch;
    const onSectionSaved = vi.fn();
    renderTab({
      letterData: RAW,
      initialBody: effectiveLetterBody(RAW, OVERRIDES),
      openBodyNonce: 1,
      onSectionSaved,
    });
    const textarea = screen.getByTestId("cl-body-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("I built platforms.\n\nI led settlement for merchants.");
    fireEvent.change(textarea, { target: { value: textarea.value.replace("settlement", "payouts") } });
    fireEvent.click(screen.getByTestId("cl-save-body-btn"));
    await waitFor(() => expect(onSectionSaved).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.section).toBe("body");
    expect(body.content).not.toContain("European e-commerce");
    expect(body.content).toContain("I led payouts for merchants.");
  });

  it("without an override the generated paragraphs are the body", () => {
    expect(effectiveLetterBody(RAW, null)).toBe(
      "I built European e-commerce platforms.\n\nI led settlement for merchants.",
    );
    expect(effectiveLetterBody(RAW, {})).toBe(effectiveLetterBody(RAW, null));
  });

  it("splits the override exactly like the backend (blank line with spaces, CR only, empties dropped)", () => {
    expect(overrideParagraphs("A\n \t\nB\r\rC\n\n\n\n")).toEqual(["A", "B", "C"]);
    expect(overrideParagraphs("single")).toEqual(["single"]);
  });
});
