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

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { AssistMicroSession } from "../AssistMicroSession";
import { withIntl } from "@/lib/test-utils/with-intl";

const GAP = { id: "Python", label: "Python" };
const BASE_PROPS = {
  cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  sectionId: "introduction",
  gap: GAP,
  onAccept: vi.fn(),
  onEdit: vi.fn(),
  onReject: vi.fn(),
};

describe("AssistMicroSession", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading state then renders the question", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "s1", question: "Wie lange Python?" }),
    } as Response);

    render(withIntl(<AssistMicroSession {...BASE_PROPS} />));
    // Initially shows loading
    expect(screen.getByTestId("assist-loading")).toBeTruthy();

    await screen.findByTestId("assist-question");
    expect(screen.getByTestId("assist-question").textContent).toContain("Wie lange Python?");
  });

  it("submitting answer shows suggestion with Accept/Edit/Reject", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: "s1", question: "Wie lange Python?" }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ suggestion: "Erfahrener Python-Entwickler." }),
      } as Response);

    render(withIntl(<AssistMicroSession {...BASE_PROPS} />));
    await screen.findByTestId("assist-answer");
    fireEvent.change(screen.getByTestId("assist-answer"), {
      target: { value: "5 Jahre" },
    });
    fireEvent.click(screen.getByTestId("assist-submit"));

    await screen.findByTestId("assist-accept");
    expect(screen.getByTestId("assist-accept")).toBeTruthy();
    expect(screen.getByTestId("assist-edit")).toBeTruthy();
    expect(screen.getByTestId("assist-reject")).toBeTruthy();
  });

  it("Accept calls onAccept with suggestion", async () => {
    const onAccept = vi.fn();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: "s1", question: "Frage?" }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ suggestion: "Verbessert." }),
      } as Response);

    render(withIntl(<AssistMicroSession {...BASE_PROPS} onAccept={onAccept} />));
    await screen.findByTestId("assist-answer");
    fireEvent.change(screen.getByTestId("assist-answer"), { target: { value: "Antwort" } });
    fireEvent.click(screen.getByTestId("assist-submit"));
    await screen.findByTestId("assist-accept");
    fireEvent.click(screen.getByTestId("assist-accept"));
    expect(onAccept).toHaveBeenCalledWith("Verbessert.");
  });

  it("Edit calls onEdit with suggestion", async () => {
    const onEdit = vi.fn();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: "s1", question: "Frage?" }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ suggestion: "Verbessert." }),
      } as Response);

    render(withIntl(<AssistMicroSession {...BASE_PROPS} onEdit={onEdit} />));
    await screen.findByTestId("assist-answer");
    fireEvent.change(screen.getByTestId("assist-answer"), { target: { value: "Antwort" } });
    fireEvent.click(screen.getByTestId("assist-submit"));
    await screen.findByTestId("assist-edit");
    fireEvent.click(screen.getByTestId("assist-edit"));
    expect(onEdit).toHaveBeenCalledWith("Verbessert.");
  });

  it("Reject calls onReject", async () => {
    const onReject = vi.fn();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: "s1", question: "Frage?" }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ suggestion: "Verbessert." }),
      } as Response);

    render(withIntl(<AssistMicroSession {...BASE_PROPS} onReject={onReject} />));
    await screen.findByTestId("assist-answer");
    fireEvent.change(screen.getByTestId("assist-answer"), { target: { value: "Antwort" } });
    fireEvent.click(screen.getByTestId("assist-submit"));
    await screen.findByTestId("assist-reject");
    fireEvent.click(screen.getByTestId("assist-reject"));
    expect(onReject).toHaveBeenCalledOnce();
  });
});

// ── M5.7.1 / ADR-040 amendment 2026-09-13 (ruling W-2) ──────────────────────
// The grounding triage withholds a sentence neither the vault nor the answer the
// candidate just typed supports, and the response carries a COUNT. Withhold-and-count
// was chosen over show-flagged because a flagged-but-visible suggestion is one paste
// away from the delivered document.
describe("AssistMicroSession — withheld grounding count", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const withSuggestion = (body: Record<string, unknown>) =>
    vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: "s1", question: "Wie lange Python?" }),
      } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => body } as Response);

  const submit = async (locale: "en" | "de" = "de") => {
    render(withIntl(<AssistMicroSession {...BASE_PROPS} />, locale));
    await screen.findByTestId("assist-question");
    fireEvent.change(screen.getByTestId("assist-answer"), {
      target: { value: "Seit 2019." },
    });
    fireEvent.click(screen.getByTestId("assist-submit"));
  };

  it("names the withheld sentences when the triage withheld some", async () => {
    withSuggestion({ suggestion: "Erfahrener Python-Entwickler.", withheld_count: 2 });
    // Rendered in GERMAN on purpose: German is the default UI language, and the
    // catalogue's job is to be real German rather than the English string copied over —
    // which key parity cannot tell you.
    await submit("de");
    const note = await screen.findByTestId("assist-withheld");
    expect(note.textContent).toContain("2 Sätze");
    expect(note.textContent).toContain("zurückgehalten");
    expect(note.textContent?.toLowerCase()).not.toContain("withheld");
  });

  it("uses the singular form for one withheld sentence", async () => {
    withSuggestion({ suggestion: "Erfahrener Python-Entwickler.", withheld_count: 1 });
    await submit("de");
    const note = await screen.findByTestId("assist-withheld");
    expect(note.textContent).toContain("Ein Satz");
  });

  it("says nothing when nothing was withheld", async () => {
    withSuggestion({ suggestion: "Erfahrener Python-Entwickler.", withheld_count: 0 });
    await submit();
    await screen.findByTestId("assist-accept");
    expect(screen.queryByTestId("assist-withheld")).toBeNull();
  });

  it("stays silent for a backend that does not send the field at all", async () => {
    // Back-compatible by construction: the field defaults to 0 server-side, and an
    // older response shape must not render an empty warning.
    withSuggestion({ suggestion: "Erfahrener Python-Entwickler." });
    await submit();
    await screen.findByTestId("assist-accept");
    expect(screen.queryByTestId("assist-withheld")).toBeNull();
  });
});
