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
 * EducationEditor — US290. Mirrors WorkExperienceEditor.test.tsx; only the
 * fields differ (institution/degree required, no is_current).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EducationEditor } from "../EducationEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { EducationEntry } from "@/lib/profile-entries";

const FULL_ENTRY: EducationEntry = {
  id: "e1",
  institution: "TU Berlin",
  degree: "M.Sc. Computer Science",
  field: "Distributed Systems",
  start_date: "2015-09",
  end_date: "2018-07",
  grade: "1.3",
  thesis_title: "Consensus at scale",
  relevant_coursework: ["Distributed Systems"],
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: EducationEntry[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <EducationEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("EducationEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1
  it("echoes the entry id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [FULL_ENTRY] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("education-entry-edit-0"));
    fireEvent.click(screen.getByTestId("education-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("e1");
  });

  it("sends a new entry without an id key", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("education-add"));
    fireEvent.change(screen.getByTestId("education-field-institution"), { target: { value: "HTW Berlin" } });
    fireEvent.change(screen.getByTestId("education-field-degree"), { target: { value: "B.Sc." } });
    fireEvent.click(screen.getByTestId("education-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
  });

  // H1.3
  it("patches only the edited field, preserving every other populated field", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [FULL_ENTRY] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("education-entry-edit-0"));
    fireEvent.change(screen.getByTestId("education-coursework-item-0"), { target: { value: "Consensus Algorithms" } });
    fireEvent.click(screen.getByTestId("education-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    const { relevant_coursework: sentCourse, ...sentRest } = sent;
    const { relevant_coursework: origCourse, ...origRest } = FULL_ENTRY;
    expect(sentRest).toEqual(origRest);
    expect(sentCourse).toEqual(["Consensus Algorithms"]);
    expect(origCourse).toEqual(["Distributed Systems"]);
  });

  // H1.12 — legacy `year` (no parseable start/end date) preserved verbatim.
  it("preserves a legacy year field when only coursework is edited", async () => {
    const entry: EducationEntry = { ...FULL_ENTRY, start_date: null, end_date: null, year: "2018" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("education-entry-edit-0"));
    fireEvent.change(screen.getByTestId("education-coursework-item-0"), { target: { value: "Updated" } });
    fireEvent.click(screen.getByTestId("education-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.year).toBe("2018");
  });

  // H1.12 — an unparseable start_date is preserved verbatim too.
  it("preserves a legacy start_date verbatim when only coursework is edited", async () => {
    const entry: EducationEntry = { ...FULL_ENTRY, start_date: "circa 2015" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("education-entry-edit-0"));
    expect(screen.getByTestId("education-start-date-legacy-original").textContent).toContain("circa 2015");
    fireEvent.change(screen.getByTestId("education-coursework-item-0"), { target: { value: "Updated" } });
    fireEvent.click(screen.getByTestId("education-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.start_date).toBe("circa 2015");
  });

  // H1.5 — confirmation required before removing an entry.
  it("asks for confirmation before removing an entry", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("education-entry-remove-0"));
    expect(screen.getByTestId("education-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("education-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  // H1.8 — blank institution/degree blocks submit.
  it("refuses to submit when institution and degree are blank", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("education-add"));
    fireEvent.click(screen.getByTestId("education-entry-save"));

    expect(await screen.findByTestId("education-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.6 (409)
  it("on a 409 stale_edit, reloads from `current` and keeps the dialog open", async () => {
    const current = { updated_at: "t3", profile: { education: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("education-entry-edit-0"));
    fireEvent.click(screen.getByTestId("education-entry-save"));

    await waitFor(() => expect(screen.getByTestId("education-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("education-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
  });

  // H0.4
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_ENTRY, relevant_coursework: [] };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { education: [unchanged] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("education-entry-edit-0"));
    fireEvent.change(screen.getByTestId("education-coursework-item-0"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByTestId("education-entry-save"));

    expect(await screen.findByTestId("education-mismatch-notice")).toBeInTheDocument();
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("education-add")).toBeInTheDocument();
  });
});
