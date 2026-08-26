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

/**
 * SummaryEditor — US292 slice B. Each `it` name below is annotated with the
 * FMEA rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SummaryEditor } from "../SummaryEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { SummaryValue } from "../ProfileSectionCard";

function jsonResponse(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response;
}

function renderEditor(value: SummaryValue, uiLanguage: "de" | "en" = "en", onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <SummaryEditor
        value={value}
        uiLanguage={uiLanguage}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("SummaryEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // JF-F-H3.1 (Finetuner-Delta FMEA) — both language slots are ALWAYS
  // visible; there is no language selector to hide one of them behind.
  it("renders both language slots, never a language selector", () => {
    renderEditor({ de: "Erfahrener Ingenieur.", en: "Experienced engineer." });
    fireEvent.click(screen.getByTestId("summary-edit"));
    expect(screen.getByTestId("summary-field-de")).toBeInTheDocument();
    expect(screen.getByTestId("summary-field-en")).toBeInTheDocument();
    expect((screen.getByTestId("summary-field-de") as HTMLTextAreaElement).value).toBe(
      "Erfahrener Ingenieur.",
    );
    expect((screen.getByTestId("summary-field-en") as HTMLTextAreaElement).value).toBe(
      "Experienced engineer.",
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("sends only the DE slot when only DE is edited — EN is never sent", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { professional_summary: { de: "Neu.", en: "English." } } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.change(screen.getByTestId("summary-field-de"), { target: { value: "Neu." } });
    fireEvent.click(screen.getByTestId("summary-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ de: "Neu." });
  });

  it("sends null for a slot that is cleared (blanked to whitespace-only)", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { professional_summary: { de: null, en: "English." } } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.change(screen.getByTestId("summary-field-de"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("summary-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ de: null });
  });

  it("closes without a request when nothing changed", () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.click(screen.getByTestId("summary-save"));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("summary-dialog")).not.toBeInTheDocument();
  });

  // Legacy plain-string summaries pre-date the {de,en} shape (#178). The
  // baseline is {de:null,en:null}; the string is pre-filled into the UI
  // language's slot, so an unmodified Save already migrates it.
  it("migrates a legacy plain-string summary into the ui-language slot on first save", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { professional_summary: { de: null, en: "Legacy text." } } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor("Legacy text.", "en");

    fireEvent.click(screen.getByTestId("summary-edit"));
    expect((screen.getByTestId("summary-field-en") as HTMLTextAreaElement).value).toBe("Legacy text.");
    expect((screen.getByTestId("summary-field-de") as HTMLTextAreaElement).value).toBe("");
    fireEvent.click(screen.getByTestId("summary-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ en: "Legacy text." });
  });

  it("puts a legacy string into the DE slot when the UI language is de", () => {
    renderEditor("Legacy text.", "de");
    fireEvent.click(screen.getByTestId("summary-edit"));
    expect((screen.getByTestId("summary-field-de") as HTMLTextAreaElement).value).toBe("Legacy text.");
    expect((screen.getByTestId("summary-field-en") as HTMLTextAreaElement).value).toBe("");
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { professional_summary: { de: "Server.", en: "Server EN." } } };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.change(screen.getByTestId("summary-field-de"), { target: { value: "Neu." } });
    fireEvent.click(screen.getByTestId("summary-save"));

    await waitFor(() => expect(screen.getByTestId("summary-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("summary-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the backend message on a 422", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(422, { detail: "de must not exceed 2000 characters" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.change(screen.getByTestId("summary-field-de"), { target: { value: "Neu." } });
    fireEvent.click(screen.getByTestId("summary-save"));

    expect(await screen.findByTestId("summary-dialog-error")).toHaveTextContent(
      "de must not exceed 2000 characters",
    );
  });

  // H0.4 — a 200 with an unchanged slot surfaces the mismatch notice.
  it("shows a mismatch notice when the saved slot comes back unchanged", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { professional_summary: { de: "Alt.", en: "English." } } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.change(screen.getByTestId("summary-field-de"), { target: { value: "Neu." } });
    fireEvent.click(screen.getByTestId("summary-save"));

    expect(await screen.findByTestId("summary-mismatch-notice")).toBeInTheDocument();
  });

  it("sends exactly one PATCH on a rapid double-click of Save", async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ de: "Alt.", en: "English." });

    fireEvent.click(screen.getByTestId("summary-edit"));
    fireEvent.change(screen.getByTestId("summary-field-de"), { target: { value: "Neu." } });
    fireEvent.click(screen.getByTestId("summary-save"));
    fireEvent.click(screen.getByTestId("summary-save"));

    resolveFetch(
      jsonResponse(200, { updated_at: "t2", profile: { professional_summary: { de: "Neu.", en: "English." } } }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  // H1.13 — the focus effect must be keyed on [dialogOpen] only, never on the
  // per-keystroke-replaced draft/dialog object, or it steals focus back to
  // the DE field on every character typed into EN.
  it("keeps focus in the EN field while typing there", () => {
    renderEditor({ de: "Alt.", en: "" });
    fireEvent.click(screen.getByTestId("summary-edit"));
    const enField = screen.getByTestId("summary-field-en") as HTMLTextAreaElement;
    enField.focus();
    expect(document.activeElement).toBe(enField);

    fireEvent.change(enField, { target: { value: "H" } });
    fireEvent.change(enField, { target: { value: "He" } });
    fireEvent.change(enField, { target: { value: "Hel" } });

    expect(document.activeElement).toBe(enField);
  });
});
