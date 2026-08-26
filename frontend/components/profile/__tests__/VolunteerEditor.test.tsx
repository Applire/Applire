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
 * VolunteerEditor — US292. Each `it` name below is annotated with the FMEA
 * build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VolunteerEditor } from "../VolunteerEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { VolunteerActivity } from "@/lib/profile-entries";

const FULL_ENTRY: VolunteerActivity = {
  id: "v1",
  organization: "Rotes Kreuz",
  role: "Ersthelfer",
  cause: "Health",
  location: "Munich",
  start_date: "2019-06",
  end_date: null,
  is_current: true,
  description: "Weekend first-aid shifts.",
  responsibilities: ["Staff the first-aid tent"],
  achievements: ["Trained 12 new volunteers"],
  technologies: [],
  expected_fields: ["cause"],
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: VolunteerActivity[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <VolunteerEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-26T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("VolunteerEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the volunteer activity id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("v1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  it("sends a new volunteer activity without an id key", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("volunteer-add"));
    fireEvent.change(screen.getByTestId("volunteer-field-organization"), { target: { value: "THW" } });
    fireEvent.change(screen.getByTestId("volunteer-field-role"), { target: { value: "Helfer" } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    expect(body[0].organization).toBe("THW");
    expect(body[0].role).toBe("Helfer");
  });

  // Required-field validation — organization AND role are both required.
  it("refuses to submit when organization or role is blank", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("volunteer-add"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));
    expect(await screen.findByTestId("volunteer-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("volunteer-field-organization"), { target: { value: "THW" } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));
    expect(await screen.findByTestId("volunteer-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("volunteer-field-organization"), { target: { value: "   " } });
    fireEvent.change(screen.getByTestId("volunteer-field-role"), { target: { value: "Helfer" } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));
    expect(await screen.findByTestId("volunteer-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.9 — clearing a date emits null, never "".
  it("emits null, never an empty string, when a date is cleared", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.change(screen.getByTestId("volunteer-start-date-year"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.start_date).toBeNull();
    expect(sent.start_date).not.toBe("");
  });

  // H1.10 — is_current: null round-trips untouched when the user doesn't act.
  it("leaves is_current: null untouched when the dialog is saved without touching status", async () => {
    const entry: VolunteerActivity = { ...FULL_ENTRY, is_current: null, end_date: "2021-01" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [entry] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    expect((screen.getByTestId("volunteer-is-current-unknown") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.is_current).toBeNull();
  });

  // H1.10 — choosing "current" sets true and clears end_date.
  it("choosing current sets is_current true and clears end_date", async () => {
    const entry: VolunteerActivity = { ...FULL_ENTRY, is_current: false, end_date: "2021-01" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [entry] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.click(screen.getByTestId("volunteer-is-current-current"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.is_current).toBe(true);
    expect(sent.end_date).toBeNull();
  });

  // H1.3 — an unknown/legacy key round-trips verbatim.
  it("round-trips unknown/legacy keys verbatim (expected_fields and a made-up legacy field)", async () => {
    const withLegacy: VolunteerActivity = { ...FULL_ENTRY, legacy_hours: 120 };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [withLegacy] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([withLegacy]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.change(screen.getByTestId("volunteer-field-location"), { target: { value: "Berlin" } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.expected_fields).toEqual(["cause"]);
    expect(sent.legacy_hours).toBe(120);
    expect(sent.location).toBe("Berlin");
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { volunteer_activities: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(screen.getByTestId("volunteer-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("volunteer-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // 422 — the string detail is shown.
  it("shows the 422 detail message inline", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "role must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    expect(await screen.findByTestId("volunteer-entry-dialog-error")).toHaveTextContent(
      "role must not be blank",
    );
  });

  // H0.4 — a 200 with an unchanged vault surfaces the mismatch notice.
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_ENTRY };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.change(screen.getByTestId("volunteer-field-location"), { target: { value: "Hamburg" } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    expect(await screen.findByTestId("volunteer-mismatch-notice")).toBeInTheDocument();
  });

  // Double-submit guard — two rapid Save clicks fire exactly one PATCH.
  it("sends exactly one PATCH when Save is clicked twice quickly", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [FULL_ENTRY] } }));
    await waitFor(() => expect(screen.queryByTestId("volunteer-entry-dialog")).not.toBeInTheDocument());
  });

  // H1.13 — typing into the second field keeps focus there.
  it("keeps focus in the field being typed into", () => {
    global.fetch = vi.fn() as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    const role = screen.getByTestId("volunteer-field-role") as HTMLInputElement;
    role.focus();
    fireEvent.change(role, { target: { value: "Teamleiter" } });
    fireEvent.change(role, { target: { value: "Teamleiter II" } });

    expect(document.activeElement).toBe(role);
    expect(role.value).toBe("Teamleiter II");
    expect((screen.getByTestId("volunteer-field-organization") as HTMLInputElement).value).toBe(
      FULL_ENTRY.organization,
    );
  });

  // Adversarial rule — whitespace-only bullets are dropped, the rest trimmed.
  it("drops whitespace-only bullets and trims the rest before saving", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-edit-0"));
    fireEvent.change(screen.getByTestId("volunteer-responsibilities-item-0"), { target: { value: "  kept  " } });
    fireEvent.click(screen.getByTestId("volunteer-responsibilities-add"));
    fireEvent.change(screen.getByTestId("volunteer-responsibilities-item-1"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("volunteer-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.responsibilities).toEqual(["kept"]);
  });

  // H1.5 — removing an entry asks for confirmation first, and PATCHes only on confirm.
  it("asks for confirmation before removing a volunteer activity, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { volunteer_activities: [] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("volunteer-remove-0"));
    expect(screen.getByTestId("volunteer-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("volunteer-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([]);
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("volunteer-add")).toBeInTheDocument();
  });
});
