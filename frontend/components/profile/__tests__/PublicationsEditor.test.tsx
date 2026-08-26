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
 * PublicationsEditor — US292. Each `it` name below is annotated with the
 * FMEA build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PublicationsEditor } from "../PublicationsEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { Publication } from "@/lib/profile-entries";

const FULL_ENTRY: Publication = {
  id: "pub1",
  title: "Scalable Event Sourcing for CV Pipelines",
  type: "publication",
  co_authors: ["Jane Doe"],
  venue: "ICSE 2023",
  published_date: "2023-04",
  doi: "10.1000/xyz123",
  url: "https://example.com/paper",
  patent_number: null,
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: Publication[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <PublicationsEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-26T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("PublicationsEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the publication id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("pub1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  it("sends a new publication without an id key", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { publications: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("publications-add"));
    fireEvent.change(screen.getByTestId("publication-field-title"), { target: { value: "New Paper" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    expect(body[0].title).toBe("New Paper");
  });

  // Required-field validation — a blank/whitespace-only title is refused.
  it("refuses to submit when the title is blank or whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("publications-add"));
    fireEvent.click(screen.getByTestId("publication-entry-save"));
    expect(await screen.findByTestId("publication-entry-validation-error")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("publication-field-title"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));
    expect(await screen.findByTestId("publication-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H2.3 — a year alone is coerced to 1 January by the backend `date` field —
  // the editor refuses year-only dates, exactly like CertificationsEditor.
  it("refuses a year-only published_date so no January is fabricated", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.change(screen.getByTestId("publication-published-date-month"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    expect(await screen.findByTestId("publication-entry-validation-error")).toHaveTextContent("month");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H2.3 — a full "YYYY-MM" date is accepted and round-trips through the picker.
  it("accepts a YYYY-MM published_date and re-emits it unchanged", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    expect((screen.getByTestId("publication-published-date-year") as HTMLInputElement).value).toBe("2023");
    expect((screen.getByTestId("publication-published-date-month") as HTMLSelectElement).value).toBe("04");
    fireEvent.change(screen.getByTestId("publication-published-date-month"), { target: { value: "04" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.published_date).toBe("2023-04");
  });

  // H1.9 — clearing the published_date emits null, never "".
  it("emits null, never an empty string, when published_date is cleared", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.change(screen.getByTestId("publication-published-date-year"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("publication-published-date-month"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.published_date).toBeNull();
    expect(sent.published_date).not.toBe("");
  });

  // H1.3 — an unknown/legacy key round-trips verbatim.
  it("round-trips an unknown/legacy key verbatim", async () => {
    const withLegacy: Publication = { ...FULL_ENTRY, legacy_note: "from ORCID import" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [withLegacy] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([withLegacy]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.change(screen.getByTestId("publication-field-venue"), { target: { value: "ICSE 2024" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.legacy_note).toBe("from ORCID import");
    expect(sent.venue).toBe("ICSE 2024");
  });

  // Type select — switching to "patent" is sent verbatim.
  it("sends the selected type (patent) and patent number", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.change(screen.getByTestId("publication-field-type"), { target: { value: "patent" } });
    fireEvent.change(screen.getByTestId("publication-field-patent-number"), { target: { value: "EP1234567" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.type).toBe("patent");
    expect(sent.patent_number).toBe("EP1234567");
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { publications: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(screen.getByTestId("publication-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("publication-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // 422 — the string detail is shown.
  it("shows the 422 detail message inline", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "title must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    expect(await screen.findByTestId("publication-entry-dialog-error")).toHaveTextContent(
      "title must not be blank",
    );
  });

  // H0.4 — a 200 with an unchanged vault surfaces the mismatch notice.
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_ENTRY };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.change(screen.getByTestId("publication-field-venue"), { target: { value: "Different Venue" } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    expect(await screen.findByTestId("publications-mismatch-notice")).toBeInTheDocument();
  });

  // Double-submit guard — two rapid Save clicks fire exactly one PATCH.
  it("sends exactly one PATCH when Save is clicked twice quickly", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.click(screen.getByTestId("publication-entry-save"));
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(jsonResponse(200, { updated_at: "t2", profile: { publications: [FULL_ENTRY] } }));
    await waitFor(() => expect(screen.queryByTestId("publication-entry-dialog")).not.toBeInTheDocument());
  });

  // H1.13 — typing into the second field keeps focus there.
  it("keeps focus in the field being typed into", () => {
    global.fetch = vi.fn() as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    const type = screen.getByTestId("publication-field-venue") as HTMLInputElement;
    type.focus();
    fireEvent.change(type, { target: { value: "New Venue" } });
    fireEvent.change(type, { target: { value: "New Venue II" } });

    expect(document.activeElement).toBe(type);
    expect(type.value).toBe("New Venue II");
    expect((screen.getByTestId("publication-field-title") as HTMLInputElement).value).toBe(FULL_ENTRY.title);
  });

  // Adversarial rule — whitespace-only co-authors are dropped, the rest trimmed.
  it("drops whitespace-only co-authors and trims the rest before saving", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { publications: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-edit-0"));
    fireEvent.change(screen.getByTestId("publication-co-authors-item-0"), { target: { value: "  Jane Doe  " } });
    fireEvent.click(screen.getByTestId("publication-co-authors-add"));
    fireEvent.change(screen.getByTestId("publication-co-authors-item-1"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("publication-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.co_authors).toEqual(["Jane Doe"]);
  });

  // H1.5 — removing an entry asks for confirmation first, and PATCHes only on confirm.
  it("asks for confirmation before removing a publication, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { publications: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("publication-remove-0"));
    expect(screen.getByTestId("publication-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("publication-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([]);
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("publications-add")).toBeInTheDocument();
  });

  it("shows a type badge only for patents", () => {
    renderEditor([FULL_ENTRY, { ...FULL_ENTRY, id: "pub2", type: "patent", title: "A Patent" }]);
    expect(screen.queryByTestId("publication-type-badge-0")).not.toBeInTheDocument();
    expect(screen.getByTestId("publication-type-badge-1")).toBeInTheDocument();
  });
});
