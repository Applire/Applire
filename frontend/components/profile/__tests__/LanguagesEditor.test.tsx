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
 * LanguagesEditor — US291. Each `it` name below is annotated with the FMEA
 * build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LanguagesEditor } from "../LanguagesEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { Language } from "@/lib/profile-entries";

const FULL_LANGUAGE: Language = {
  id: "l1",
  language: "German",
  level: "C1",
  status: "confirmed",
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: Language[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <LanguagesEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("LanguagesEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the language id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { languages: [FULL_LANGUAGE] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_LANGUAGE]);

    fireEvent.click(screen.getByTestId("language-edit-0"));
    fireEvent.click(screen.getByTestId("language-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("l1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  // H2.2 (PO ruling 2026-08-25) — a NEW language carries an explicit
  // status: "confirmed", and the add affordance reads "Add as confirmed".
  it("sends a new language without an id key and with an explicit confirmed status", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { languages: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    expect(screen.getByTestId("languages-add")).toHaveTextContent("Add as confirmed");
    fireEvent.click(screen.getByTestId("languages-add"));
    fireEvent.change(screen.getByTestId("language-field-language"), { target: { value: "French" } });
    fireEvent.click(screen.getByTestId("language-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    // H2.4 — the wire field is `language`, never `name`.
    expect(body[0].language).toBe("French");
    expect(body[0]).not.toHaveProperty("name");
    expect(body[0].status).toBe("confirmed");
  });

  // H2.1 — an existing entry keeps whatever status it carries; changing the
  // level of an unconfirmed language leaves it unconfirmed.
  it("leaves an unconfirmed language's status untouched when only level changes", async () => {
    const unconfirmed: Language = { ...FULL_LANGUAGE, status: "unconfirmed" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { languages: [unconfirmed] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([unconfirmed]);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("unconfirmed");
    fireEvent.click(screen.getByTestId("language-edit-0"));
    fireEvent.change(screen.getByTestId("language-field-level"), { target: { value: "C2" } });
    fireEvent.click(screen.getByTestId("language-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.status).toBe("unconfirmed");
    expect(sent.level).toBe("C2");
  });

  // H1.3 — editing one field leaves every other populated field byte-identical.
  it("patches only the edited field, preserving every other populated field", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { languages: [FULL_LANGUAGE] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_LANGUAGE]);

    fireEvent.click(screen.getByTestId("language-edit-0"));
    fireEvent.change(screen.getByTestId("language-field-level"), { target: { value: "Native" } });
    fireEvent.click(screen.getByTestId("language-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    const { level: sentLevel, ...sentRest } = sent;
    const { level: origLevel, ...origRest } = FULL_LANGUAGE;
    expect(sentRest).toEqual(origRest);
    expect(sentLevel).toBe("Native");
    expect(origLevel).toBe("C1");
  });

  // A legacy level the CEFR/Native select doesn't recognise is offered as an
  // extra option and preserved verbatim when the entry is saved unchanged.
  it("preserves a legacy level verbatim as an extra select option", async () => {
    const legacy: Language = { ...FULL_LANGUAGE, level: "fließend" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { languages: [legacy] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([legacy]);

    fireEvent.click(screen.getByTestId("language-edit-0"));
    const select = screen.getByTestId("language-field-level") as HTMLSelectElement;
    expect(select.value).toBe("fließend");
    fireEvent.click(screen.getByTestId("language-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.level).toBe("fließend");
  });

  // H1.5 — removing an entry asks for confirmation first.
  it("asks for confirmation before removing a language, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { languages: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_LANGUAGE]);

    fireEvent.click(screen.getByTestId("language-remove-0"));
    expect(screen.getByTestId("language-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("language-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  // Rule 6 (H2.4) — required-field validation refuses a blank/whitespace-only
  // language before any request.
  it("refuses to submit when the language is blank or whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("languages-add"));
    fireEvent.click(screen.getByTestId("language-entry-save"));
    expect(await screen.findByTestId("language-entry-validation-error")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("language-field-language"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("language-entry-save"));
    expect(await screen.findByTestId("language-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { languages: [FULL_LANGUAGE] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_LANGUAGE]);

    fireEvent.click(screen.getByTestId("language-edit-0"));
    fireEvent.click(screen.getByTestId("language-entry-save"));

    await waitFor(() => expect(screen.getByTestId("language-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("language-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // H0.4 — a 200 with an unchanged vault surfaces the mismatch notice.
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_LANGUAGE };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { languages: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_LANGUAGE]);

    fireEvent.click(screen.getByTestId("language-edit-0"));
    fireEvent.change(screen.getByTestId("language-field-level"), { target: { value: "C2" } });
    fireEvent.click(screen.getByTestId("language-entry-save"));

    expect(await screen.findByTestId("languages-mismatch-notice")).toBeInTheDocument();
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("languages-add")).toBeInTheDocument();
  });
});
