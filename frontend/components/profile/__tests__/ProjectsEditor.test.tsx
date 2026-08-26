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
 * ProjectsEditor — US292. Each `it` name below is annotated with the FMEA
 * build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectsEditor } from "../ProjectsEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { ProjectEntry } from "@/lib/profile-entries";

const FULL_ENTRY: ProjectEntry = {
  id: "p1",
  name: "Internal Tooling Revamp",
  role: "Tech Lead",
  associated_experience: "TechVision GmbH",
  location: "Berlin",
  start_date: "2021-03",
  end_date: null,
  is_current: true,
  description: "Rebuilt the internal deployment tooling.",
  url: "https://example.com/project",
  responsibilities: ["Own the rollout plan"],
  achievements: ["Cut deploy time by 50%"],
  technologies: ["Python", "Terraform"],
  expected_fields: ["technologies"],
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: ProjectEntry[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <ProjectsEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-26T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("ProjectsEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the project id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { projects: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("p1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  it("sends a new project without an id key", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { projects: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("projects-add"));
    fireEvent.change(screen.getByTestId("project-field-name"), { target: { value: "New Project" } });
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    expect(body[0].name).toBe("New Project");
  });

  // Required-field validation — a blank/whitespace-only name is refused.
  it("refuses to submit when the name is blank or whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("projects-add"));
    fireEvent.click(screen.getByTestId("project-entry-save"));
    expect(await screen.findByTestId("project-entry-validation-error")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("project-field-name"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("project-entry-save"));
    expect(await screen.findByTestId("project-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.9 — clearing a date emits null, never "".
  it("emits null, never an empty string, when a date is cleared", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { projects: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.change(screen.getByTestId("project-start-date-year"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.start_date).toBeNull();
    expect(sent.start_date).not.toBe("");
  });

  // H1.10 — is_current: null round-trips untouched when the user doesn't act.
  it("leaves is_current: null untouched when the dialog is saved without touching status", async () => {
    const entry: ProjectEntry = { ...FULL_ENTRY, is_current: null, end_date: "2022-01" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { projects: [entry] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([entry]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    expect((screen.getByTestId("project-is-current-unknown") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.is_current).toBeNull();
  });

  // H1.3 — an unknown/legacy key round-trips verbatim.
  it("round-trips unknown/legacy keys verbatim (expected_fields and a made-up legacy field)", async () => {
    const withLegacy: ProjectEntry = { ...FULL_ENTRY, legacy_note: "imported from LinkedIn" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { projects: [withLegacy] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([withLegacy]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.change(screen.getByTestId("project-field-location"), { target: { value: "Munich" } });
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.expected_fields).toEqual(["technologies"]);
    expect(sent.legacy_note).toBe("imported from LinkedIn");
    expect(sent.location).toBe("Munich");
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { projects: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(screen.getByTestId("project-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("project-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // 422 — the string detail is shown.
  it("shows the 422 detail message inline", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "name must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.click(screen.getByTestId("project-entry-save"));

    expect(await screen.findByTestId("project-entry-dialog-error")).toHaveTextContent("name must not be blank");
  });

  // H0.4 — a 200 with an unchanged vault surfaces the mismatch notice.
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_ENTRY };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { projects: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.change(screen.getByTestId("project-field-location"), { target: { value: "Hamburg" } });
    fireEvent.click(screen.getByTestId("project-entry-save"));

    expect(await screen.findByTestId("projects-mismatch-notice")).toBeInTheDocument();
  });

  // Double-submit guard — two rapid Save clicks fire exactly one PATCH.
  it("sends exactly one PATCH when Save is clicked twice quickly", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.click(screen.getByTestId("project-entry-save"));
    fireEvent.click(screen.getByTestId("project-entry-save"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(jsonResponse(200, { updated_at: "t2", profile: { projects: [FULL_ENTRY] } }));
    await waitFor(() => expect(screen.queryByTestId("project-entry-dialog")).not.toBeInTheDocument());
  });

  // H1.13 — typing into the second field keeps focus there (focus effect
  // must be keyed on open-state/index, never the per-keystroke draft object).
  it("keeps focus in the field being typed into", () => {
    global.fetch = vi.fn() as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    const role = screen.getByTestId("project-field-role") as HTMLInputElement;
    role.focus();
    fireEvent.change(role, { target: { value: "Staff Engineer" } });
    fireEvent.change(role, { target: { value: "Staff Engineer II" } });

    expect(document.activeElement).toBe(role);
    expect(role.value).toBe("Staff Engineer II");
    expect((screen.getByTestId("project-field-name") as HTMLInputElement).value).toBe(FULL_ENTRY.name);
  });

  // Adversarial rule — whitespace-only bullets are dropped, the rest trimmed.
  it("drops whitespace-only bullets and trims the rest before saving", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { projects: [FULL_ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.change(screen.getByTestId("project-responsibilities-item-0"), { target: { value: "  kept  " } });
    fireEvent.click(screen.getByTestId("project-responsibilities-add"));
    fireEvent.change(screen.getByTestId("project-responsibilities-item-1"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("project-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.responsibilities).toEqual(["kept"]);
  });

  // H1.5 — removing an entry asks for confirmation first, and PATCHes only on confirm.
  it("asks for confirmation before removing a project, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { projects: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-remove-0"));
    expect(screen.getByTestId("project-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("project-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([]);
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("projects-add")).toBeInTheDocument();
  });

  it("cancelling the remove confirmation sends nothing", () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-remove-0"));
    fireEvent.click(screen.getByTestId("project-entry-remove-cancel"));
    expect(screen.queryByTestId("project-entry-remove-dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Adversarial pass 2026-08-26 — F1 (blocker, reproduced live: the user
  // selected "Project Beta", the retry deleted an unrelated entry). A
  // stale-conflict retry on Remove must never leave the confirm dialog open
  // on the same index.
  it("on a 409 during confirmRemove, closes the confirm dialog, reloads via onProfileUpdated, and shows a list-level stale notice", async () => {
    const current = { updated_at: "t3", profile: { projects: [FULL_ENTRY] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-remove-0"));
    fireEvent.click(screen.getByTestId("project-entry-remove-confirm"));

    await waitFor(() => expect(screen.getByTestId("projects-stale-notice")).toBeInTheDocument());
    expect(screen.queryByTestId("project-entry-remove-dialog")).not.toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // F1 — the removal is keyed on the entry's id, not on its position. This is
  // the exact live-reproduced defect: "Project Beta" selected, but an
  // index-based filter removed whatever now sat at that position.
  it("removes 'Project Beta' by id even when the list has been reordered since the confirm dialog opened", async () => {
    const projectAlpha: ProjectEntry = { ...FULL_ENTRY, id: "p-alpha", name: "Project Alpha" };
    const projectBeta: ProjectEntry = { ...FULL_ENTRY, id: "p-beta", name: "Project Beta" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { projects: [projectAlpha] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    const { rerender } = render(withIntl(
      <ProjectsEditor
        entries={[projectAlpha, projectBeta]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    // The user opens the confirm dialog on "Project Beta" (index 1)...
    fireEvent.click(screen.getByTestId("project-remove-1"));

    // ...then the list reorders before they confirm.
    rerender(withIntl(
      <ProjectsEditor
        entries={[projectBeta, projectAlpha]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    fireEvent.click(screen.getByTestId("project-entry-remove-confirm"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    // An index-based filter would have removed whatever sits at index 1 AFTER
    // the reorder (projectAlpha) — leaving Beta behind by mistake.
    expect(sent).toEqual([projectAlpha]);
  });

  // F1c — the confirm dialog names the entry being removed.
  it("names the entry in the remove confirmation body", () => {
    renderEditor([FULL_ENTRY]);
    fireEvent.click(screen.getByTestId("project-remove-0"));
    expect(screen.getByTestId("project-entry-remove-dialog").textContent).toContain(
      "Internal Tooling Revamp",
    );
  });

  // F2 (major) — a failed save leaves focus outside the dialog; Escape must
  // still close it via a document-level listener.
  it("closes the dialog on a document-level Escape after a failed (422) save", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "name must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_ENTRY]);

    fireEvent.click(screen.getByTestId("project-edit-0"));
    fireEvent.click(screen.getByTestId("project-entry-save"));
    await screen.findByTestId("project-entry-dialog-error");

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByTestId("project-entry-dialog")).not.toBeInTheDocument();
  });

  // F4 — two id-less legacy entries (id: "") must not collide on their React key.
  it("renders two rows for two projects sharing an empty id, without a key warning", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const a: ProjectEntry = { ...FULL_ENTRY, id: "", name: "Alpha Project" };
    const b: ProjectEntry = { ...FULL_ENTRY, id: "", name: "Beta Project" };
    renderEditor([a, b]);

    expect(screen.getByTestId("project-edit-0")).toBeInTheDocument();
    expect(screen.getByTestId("project-edit-1")).toBeInTheDocument();
    const keyWarning = errorSpy.mock.calls.some((call) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
    expect(keyWarning).toBe(false);
    errorSpy.mockRestore();
  });
});
