// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * #737 (Strawberry build 2, WP-E) — the Edit tab's context strip, the save
 * receipt, the one new-version confirmation, the unsaved-draft dialog and the
 * letter's honest "look" line.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { EditContextStrip } from "../EditContextStrip";
import { NewVersionDialog } from "../NewVersionDialog";
import { UnsavedEditDialog } from "../UnsavedEditDialog";
import { LetterLookLine } from "../LetterLookLine";

describe("EditContextStrip", () => {
  it("renders nothing without a context or a receipt", () => {
    const { container } = render(withIntl(<EditContextStrip context={null} receipt={null} />, "de"));
    expect(container.innerHTML).toBe("");
  });

  it("names the finding and the matched wording, with a way back", () => {
    const onBack = vi.fn();
    render(
      withIntl(
        <EditContextStrip
          context={{ kind: "finding", label: "Industrie 4.0", form: "MES-Einführung" }}
          receipt={null}
          onBack={onBack}
        />,
        "de",
      ),
    );
    const strip = screen.getByTestId("edit-context-finding");
    expect(strip.textContent).toContain("Aus der Prüfung");
    expect(screen.getByTestId("edit-context-label").textContent).toBe("Industrie 4.0");
    expect(strip.textContent).toContain("„MES-Einführung“ ist nicht durch Dein Profil gedeckt");
    fireEvent.click(screen.getByTestId("edit-context-back"));
    expect(onBack).toHaveBeenCalledOnce();
    expect(screen.getByTestId("edit-context-back").textContent).toContain("Zurück zur Prüfung");
  });

  it("states the group-2 trade as the user's own writing", () => {
    render(withIntl(<EditContextStrip context={{ kind: "gap", label: "SAP PP" }} receipt={null} />, "en"));
    expect(screen.getByTestId("edit-context-gap").textContent).toContain(
      "Your profile covers “SAP PP” — it was cut for length. If you put it back here, you write it yourself.",
    );
  });

  it("keeps the letter-only facts in view and goes back to the letter", () => {
    render(
      withIntl(
        <EditContextStrip
          context={{ kind: "letter", facts: ["Kosmetik-Verpackungen", "ISO-9001-Audit-Praxis"] }}
          receipt={null}
          onBack={() => {}}
          onDismiss={() => {}}
        />,
        "de",
      ),
    );
    const facts = screen.getByTestId("edit-context-facts");
    expect(facts.textContent).toContain("Kosmetik-Verpackungen");
    expect(facts.textContent).toContain("ISO-9001-Audit-Praxis");
    expect(screen.getByTestId("edit-context-back").textContent).toContain("Zurück zum Anschreiben");
  });

  it("dismiss calls back", () => {
    const onDismiss = vi.fn();
    render(withIntl(<EditContextStrip context={{ kind: "gap", label: "x" }} receipt={null} onDismiss={onDismiss} />));
    fireEvent.click(screen.getByTestId("edit-context-dismiss"));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("receipt: cleared finding with the open count", () => {
    render(
      withIntl(
        <EditContextStrip
          context={null}
          receipt={{ kind: "finding", label: "Industrie 4.0", stillListed: false, openCount: 0 }}
        />,
        "de",
      ),
    );
    const r = screen.getByTestId("edit-receipt-cleared");
    expect(r.textContent).toContain("„Industrie 4.0“ ist in der Prüfung nicht mehr markiert.");
    expect(r.textContent).toContain("Keine Stelle mehr offen.");
  });

  it("receipt: still flagged is said, never as cleared", () => {
    render(
      withIntl(
        <EditContextStrip context={null} receipt={{ kind: "finding", label: "X", stillListed: true, openCount: 2 }} />,
        "de",
      ),
    );
    expect(screen.queryByTestId("edit-receipt-cleared")).toBeNull();
    const r = screen.getByTestId("edit-receipt-still");
    expect(r.textContent).toContain("weiterhin markiert");
    expect(r.textContent).toContain("Noch 2 Stellen offen.");
  });

  it("receipt: a plain save says the review runs again", () => {
    render(withIntl(<EditContextStrip context={null} receipt={{ kind: "plain" }} />, "en"));
    expect(screen.getByTestId("edit-receipt-plain").textContent).toBe(
      "Saved. The preview is updated and the review is running again.",
    );
  });
});

describe("NewVersionDialog", () => {
  it("names the edited sections that will not carry over", () => {
    const onConfirm = vi.fn();
    render(
      withIntl(
        <NewVersionDialog open editedSections={["Einleitung", "Fähigkeiten"]} onConfirm={onConfirm} onCancel={() => {}} />,
        "de",
      ),
    );
    expect(screen.getByTestId("edit-new-version-loss").textContent).toBe(
      "Nicht übernommen werden Deine Bearbeitungen in: Einleitung, Fähigkeiten.",
    );
    fireEvent.click(screen.getByTestId("edit-new-version-confirm"));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("asks without a loss line when nothing was edited", () => {
    const onCancel = vi.fn();
    render(withIntl(<NewVersionDialog open editedSections={[]} onConfirm={() => {}} onCancel={onCancel} />, "en"));
    expect(screen.getByTestId("edit-new-version-dialog")).toBeTruthy();
    expect(screen.queryByTestId("edit-new-version-loss")).toBeNull();
    fireEvent.click(screen.getByTestId("edit-new-version-cancel"));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("renders nothing when closed", () => {
    render(withIntl(<NewVersionDialog open={false} editedSections={["a"]} onConfirm={() => {}} onCancel={() => {}} />));
    expect(screen.queryByTestId("edit-new-version-dialog")).toBeNull();
  });
});

describe("UnsavedEditDialog", () => {
  it("names the section and offers save / discard / stay", () => {
    const onSave = vi.fn();
    const onDiscard = vi.fn();
    const onStay = vi.fn();
    render(
      withIntl(
        <UnsavedEditDialog open sectionLabel="Einleitung" onSave={onSave} onDiscard={onDiscard} onStay={onStay} />,
        "de",
      ),
    );
    expect(screen.getByTestId("edit-unsaved-dialog").textContent).toContain("Du hast „Einleitung“ geändert");
    fireEvent.click(screen.getByTestId("edit-unsaved-save"));
    fireEvent.click(screen.getByTestId("edit-unsaved-discard"));
    fireEvent.click(screen.getByTestId("edit-unsaved-stay"));
    expect([onSave, onDiscard, onStay].map((f) => f.mock.calls.length)).toEqual([1, 1, 1]);
  });

  it("shows the save failure", () => {
    render(
      withIntl(<UnsavedEditDialog open failed sectionLabel="x" onSave={() => {}} onDiscard={() => {}} onStay={() => {}} />),
    );
    expect(screen.getByText("Saving failed. Please try again.")).toBeTruthy();
  });
});

describe("LetterLookLine", () => {
  it("says the letter follows the CV's template and links there", () => {
    render(withIntl(<LetterLookLine template="modern_swiss" cvEditHref="/flow/f/cv?tab=edit" />, "de"));
    expect(screen.getByTestId("letter-look").textContent).toContain(
      "Vorlage und Farbe übernimmt das Anschreiben von Deinem Lebenslauf: Modern (Schweiz).",
    );
    expect(screen.getByTestId("letter-look-cv-link").getAttribute("href")).toBe("/flow/f/cv?tab=edit");
    // No template buttons — the old list discarded the choice.
    expect(screen.queryByTestId("cl-template-classic_german")).toBeNull();
  });

  it("falls back to the classic name for an unknown template id", () => {
    render(withIntl(<LetterLookLine template="nope" cvEditHref={null} />, "en"));
    expect(screen.getByTestId("letter-look").textContent).toContain("from your CV: Classic.");
    expect(screen.queryByTestId("letter-look-cv-link")).toBeNull();
  });
});
