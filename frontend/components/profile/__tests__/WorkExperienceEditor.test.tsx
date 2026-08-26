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
 * WorkExperienceEditor — US290. Each `it` name below is annotated with the
 * FMEA build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkExperienceEditor } from "../WorkExperienceEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { WorkEntry } from "@/lib/profile-entries";

const FULL_ENTRY: WorkEntry = {
  id: "w1",
  company: "Acme GmbH",
  role: "Senior Engineer",
  location: "Berlin",
  start_date: "2020-03",
  end_date: null,
  is_current: true,
  responsibilities: ["Own the platform roadmap"],
  achievements: ["Cut build times by 40%"],
  technologies: ["Python", "Kubernetes"],
  role_aliases: ["Tech Lead"],
  industry_context: "fintech",
  team_size: 8,
  budget_managed: "500000 EUR",
  expected_fields: ["team_size"],
  role_fact_projections: { seniority: "senior" },
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: WorkEntry[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <WorkExperienceEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("WorkExperienceEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the entry id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [FULL_ENTRY] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("w1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  it("sends a new entry without an id key", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("work-experience-add"));
    fireEvent.change(screen.getByTestId("work-field-company"), { target: { value: "NewCo" } });
    fireEvent.change(screen.getByTestId("work-field-role"), { target: { value: "Engineer" } });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    expect(body[0].company).toBe("NewCo");
  });

  // H1.3 — editing one bullet leaves every other field byte-identical.
  it("patches only the edited field, preserving every other populated field", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [FULL_ENTRY] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.change(screen.getByTestId("work-achievements-item-0"), {
      target: { value: "Cut build times by 60%" },
    });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    const { achievements: sentAchievements, ...sentRest } = sent;
    const { achievements: origAchievements, ...origRest } = FULL_ENTRY;
    expect(sentRest).toEqual(origRest);
    expect(sentAchievements).toEqual(["Cut build times by 60%"]);
    expect(origAchievements).toEqual(["Cut build times by 40%"]);
  });

  // H1.10 — is_current: null round-trips untouched when the user doesn't act.
  it("leaves is_current: null untouched when the dialog is saved without touching status", async () => {
    const entry: WorkEntry = { ...FULL_ENTRY, is_current: null, end_date: "2022-01" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    expect((screen.getByTestId("work-is-current-unknown") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.is_current).toBeNull();
  });

  // H1.10 — choosing "aktuell" sets true and clears end_date.
  it("choosing current sets is_current true and clears end_date", async () => {
    const entry: WorkEntry = { ...FULL_ENTRY, is_current: false, end_date: "2022-01" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-is-current-current"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.is_current).toBe(true);
    expect(sent.end_date).toBeNull();
  });

  // H1.10 — choosing "beendet" sets false.
  it("choosing ended sets is_current false", async () => {
    const entry: WorkEntry = { ...FULL_ENTRY, is_current: true, end_date: null };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-is-current-ended"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.is_current).toBe(false);
  });

  // H1.12 — a legacy date the picker can't parse survives an unrelated edit.
  it("preserves a legacy start_date verbatim when only a bullet is edited", async () => {
    const entry: WorkEntry = { ...FULL_ENTRY, start_date: "Q3 2019" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    expect(screen.getByTestId("work-start-date-legacy-original").textContent).toContain("Q3 2019");
    fireEvent.change(screen.getByTestId("work-achievements-item-0"), { target: { value: "Updated" } });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.start_date).toBe("Q3 2019");
  });

  // H1.5 — removing an entry asks for confirmation first.
  it("asks for confirmation before removing an entry, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-remove-0"));
    expect(screen.getByTestId("work-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("work-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([]);
  });

  it("cancelling the remove confirmation sends nothing", () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-remove-0"));
    fireEvent.click(screen.getByTestId("work-entry-remove-cancel"));
    expect(screen.queryByTestId("work-entry-remove-dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.8 — blank company/role blocks submit with an inline message.
  it("refuses to submit when company and role are blank", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("work-experience-add"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    expect(await screen.findByTestId("work-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses to submit when company/role are whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("work-experience-add"));
    fireEvent.change(screen.getByTestId("work-field-company"), { target: { value: "   " } });
    fireEvent.change(screen.getByTestId("work-field-role"), { target: { value: "Engineer" } });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    expect(await screen.findByTestId("work-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.6 — basis_updated_at flows from the prop into the PATCH query string.
  it("PATCHes with the section's basis_updated_at", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [FULL_ENTRY] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("basis_updated_at=2026-08-25T09%3A00%3A00Z");
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { work_experience: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () =>
      jsonResponse(409, { detail: { error: "stale_edit", current } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(screen.getByTestId("work-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("work-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // 422 — the string detail is shown.
  it("shows the 422 detail message inline", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "role must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    expect(await screen.findByTestId("work-entry-dialog-error")).toHaveTextContent("role must not be blank");
  });

  // H0.4 — a 200 with an unchanged vault surfaces "Nicht gespeichert".
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_ENTRY, achievements: [] };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.change(screen.getByTestId("work-achievements-item-0"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    expect(await screen.findByTestId("work-experience-mismatch-notice")).toBeInTheDocument();
  });

  it("shows no mismatch notice when the save round-trips correctly", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [{ ...FULL_ENTRY, achievements: ["Changed"] }] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.change(screen.getByTestId("work-achievements-item-0"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("work-experience-mismatch-notice")).not.toBeInTheDocument();
  });

  // F8 — internal plumbing never leaks into the read-only list.
  it("never renders internal fields (id, role_aliases) in the list view", () => {
    renderEditor([FULL_ENTRY]);
    expect(screen.queryByText(/w1/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Tech Lead/);
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("work-experience-add")).toBeInTheDocument();
  });

  // Adversarial pass 2026-08-25 — blocker: the focus effect re-fired on every
  // keystroke and stole focus back to the first field mid-word.
  it("keeps focus in the field being typed into", async () => {
    global.fetch = vi.fn() as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    const role = screen.getByTestId("work-field-role") as HTMLInputElement;
    role.focus();
    fireEvent.change(role, { target: { value: "Staff Engineer" } });
    fireEvent.change(role, { target: { value: "Staff Engineer II" } });

    expect(document.activeElement).toBe(role);
    expect(role.value).toBe("Staff Engineer II");
    expect((screen.getByTestId("work-field-company") as HTMLInputElement).value).toBe(FULL_ENTRY.company);
  });

  // Adversarial pass 2026-08-25 — major: two rapid Save clicks fired two PATCHes.
  it("sends exactly one PATCH when Save is clicked twice quickly", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-entry-save"));
    fireEvent.click(screen.getByTestId("work-entry-save"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(jsonResponse(200, { updated_at: "t2", profile: { work_experience: [FULL_ENTRY] } }));
    await waitFor(() => expect(screen.queryByTestId("work-entry-dialog")).not.toBeInTheDocument());
  });

  // Adversarial pass 2026-08-25 — minor: whitespace-only bullets were persisted.
  it("drops whitespace-only bullets and trims the rest before saving", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [FULL_ENTRY] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.change(screen.getByTestId("work-responsibilities-item-0"), { target: { value: "  kept  " } });
    fireEvent.click(screen.getByTestId("work-responsibilities-add"));
    fireEvent.change(screen.getByTestId("work-responsibilities-item-1"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("work-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.responsibilities).toEqual(["kept"]);
  });

  // Adversarial pass 2026-08-26 — F1 (blocker): a stale-conflict retry on
  // Remove must never leave the confirm dialog open on the same index.
  it("on a 409 during confirmRemove, closes the confirm dialog, reloads via onProfileUpdated, and shows a list-level stale notice", async () => {
    const current = { updated_at: "t3", profile: { work_experience: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-remove-0"));
    fireEvent.click(screen.getByTestId("work-entry-remove-confirm"));

    await waitFor(() => expect(screen.getByTestId("work-experience-stale-notice")).toBeInTheDocument());
    expect(screen.queryByTestId("work-entry-remove-dialog")).not.toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // F1 — the removal is keyed on the entry's id, not on the row's position at
  // the moment the confirm dialog is confirmed.
  it("removes the entry by id even when the list has been reordered since the confirm dialog opened", async () => {
    const other: WorkEntry = { ...FULL_ENTRY, id: "w2", company: "Other GmbH", role: "PM" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { work_experience: [other] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { rerender } = render(withIntl(
      <WorkExperienceEditor
        entries={[FULL_ENTRY, other]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    // Open the confirm dialog on `other` (index 1)...
    fireEvent.click(screen.getByTestId("work-entry-remove-1"));

    // ...then the entries prop reorders before the user confirms (e.g. an
    // unrelated save elsewhere reloaded the section).
    rerender(withIntl(
      <WorkExperienceEditor
        entries={[other, FULL_ENTRY]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    fireEvent.click(screen.getByTestId("work-entry-remove-confirm"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    // The user selected `other` — an index-based filter would instead have
    // dropped whatever sits at index 1 AFTER the reorder (FULL_ENTRY).
    expect(sent).toEqual([FULL_ENTRY]);
  });

  // F1c — the confirm dialog names the entry being removed.
  it("names the entry in the remove confirmation body", () => {
    renderEditor([FULL_ENTRY]);
    fireEvent.click(screen.getByTestId("work-entry-remove-0"));
    expect(screen.getByTestId("work-entry-remove-dialog").textContent).toContain(
      "Senior Engineer @ Acme GmbH",
    );
  });

  // F2 (major) — a failed save leaves focus outside the dialog; the Escape
  // keystroke must still close it via a document-level listener.
  it("closes the dialog on a document-level Escape after a failed (422) save", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "role must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("work-entry-edit-0"));
    fireEvent.click(screen.getByTestId("work-entry-save"));
    await screen.findByTestId("work-entry-dialog-error");

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByTestId("work-entry-dialog")).not.toBeInTheDocument();
  });

  // F4 — two id-less legacy entries (id: "") must not collide on their React key.
  it("renders two rows for two entries sharing an empty id, without a key warning", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const a: WorkEntry = { ...FULL_ENTRY, id: "", company: "Alpha" };
    const b: WorkEntry = { ...FULL_ENTRY, id: "", company: "Beta" };
    renderEditor([a, b]);

    expect(screen.getByTestId("work-entry-edit-0")).toBeInTheDocument();
    expect(screen.getByTestId("work-entry-edit-1")).toBeInTheDocument();
    const keyWarning = errorSpy.mock.calls.some((call) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
    expect(keyWarning).toBe(false);
    errorSpy.mockRestore();
  });
});
