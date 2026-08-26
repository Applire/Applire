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
 * SkillsEditor — US291. Each `it` name below is annotated with the FMEA
 * build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SkillsEditor } from "../SkillsEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { Skill } from "@/lib/profile-entries";

const FULL_SKILL: Skill = {
  id: "s1",
  name: "Kubernetes",
  category: "technical",
  proficiency: "advanced",
  years_experience: 5,
  source: "work:Logivia",
  last_used: "2025-06",
  experience_refs: ["exp-1"],
  status: "confirmed",
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: Skill[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <SkillsEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("SkillsEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the skill id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { skills: [FULL_SKILL] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("s1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  // H2.2 (PO ruling 2026-08-25) — a NEW skill carries an explicit
  // status: "confirmed", and the add affordance reads "Add as confirmed".
  it("sends a new skill without an id key and with an explicit confirmed status", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { skills: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    expect(screen.getByTestId("skills-add")).toHaveTextContent("Add as confirmed");
    fireEvent.click(screen.getByTestId("skills-add"));
    fireEvent.change(screen.getByTestId("skill-field-name"), { target: { value: "Rust" } });
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    expect(body[0].name).toBe("Rust");
    expect(body[0].status).toBe("confirmed");
  });

  // H2.1 — an existing entry keeps whatever status it carries; changing the
  // proficiency of an unconfirmed skill leaves it unconfirmed.
  it("leaves an unconfirmed skill's status untouched when only proficiency changes", async () => {
    const unconfirmed: Skill = { ...FULL_SKILL, status: "unconfirmed" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { skills: [unconfirmed] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([unconfirmed]);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("unconfirmed");
    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.change(screen.getByTestId("skill-field-proficiency"), { target: { value: "expert" } });
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.status).toBe("unconfirmed");
    expect(sent.proficiency).toBe("expert");
  });

  // H2.1 — a denied skill's chip shows the read-only badge and is editable
  // only for removal: the edit action is disabled, remove still works.
  it("disables editing but not removal of a denied skill", async () => {
    const denied: Skill = { ...FULL_SKILL, status: "denied" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { skills: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([denied]);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("denied");
    const editButton = screen.getByTestId("skill-edit-0") as HTMLButtonElement;
    expect(editButton.disabled).toBe(true);
    fireEvent.click(editButton);
    expect(screen.queryByTestId("skill-entry-dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("skill-remove-0"));
    fireEvent.click(screen.getByTestId("skill-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([]);
  });

  // H1.3 — editing one field leaves every other populated field byte-identical.
  it("patches only the edited field, preserving every other populated field", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { skills: [FULL_SKILL] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.change(screen.getByTestId("skill-field-years-experience"), { target: { value: "7" } });
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    const { years_experience: sentYears, ...sentRest } = sent;
    const { years_experience: origYears, ...origRest } = FULL_SKILL;
    expect(sentRest).toEqual(origRest);
    expect(sentYears).toBe(7);
    expect(origYears).toBe(5);
  });

  // H1.5 — removing an entry asks for confirmation first.
  it("asks for confirmation before removing a skill, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { skills: [] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-remove-0"));
    expect(screen.getByTestId("skill-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("skill-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  // Rule 6 — required-field validation refuses a blank/whitespace-only name
  // before any request.
  it("refuses to submit when the name is blank or whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("skills-add"));
    fireEvent.click(screen.getByTestId("skill-entry-save"));
    expect(await screen.findByTestId("skill-entry-validation-error")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("skill-field-name"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("skill-entry-save"));
    expect(await screen.findByTestId("skill-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // H1.6 — basis_updated_at flows from the prop into the PATCH query string,
  // and a 409 stale_edit reloads `current`, keeps the dialog open, and shows
  // a notice — a subsequent save then succeeds.
  it("PATCHes with basis_updated_at and recovers from a 409 stale_edit", async () => {
    const current = { updated_at: "t3", profile: { skills: [FULL_SKILL] } };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(409, { detail: { error: "stale_edit", current } }))
      .mockResolvedValueOnce(jsonResponse(200, { updated_at: "t3", profile: { skills: [FULL_SKILL] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    await waitFor(() => expect(screen.getByTestId("skill-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("skill-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("basis_updated_at=2026-08-25T09%3A00%3A00Z");

    fireEvent.click(screen.getByTestId("skill-entry-save"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("skill-entry-dialog")).not.toBeInTheDocument());
  });

  // H0.4 — a 200 with an unchanged vault surfaces the mismatch notice.
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_SKILL, years_experience: 5 };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { skills: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.change(screen.getByTestId("skill-field-years-experience"), { target: { value: "9" } });
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    expect(await screen.findByTestId("skills-mismatch-notice")).toBeInTheDocument();
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("skills-add")).toBeInTheDocument();
  });

  // H2.2 — the affordance sits on the commit button too, not only on the
  // section's add button (real-browser pass 2026-08-26: the dialog said "Save").
  it("labels the add-dialog save button with the confirmed affordance", () => {
    global.fetch = vi.fn() as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("skills-add"));
    expect(screen.getByTestId("skill-entry-save")).toHaveTextContent("Add as confirmed");
  });

  // Adversarial finding 2026-08-26 (minor): `min={0}` was decorative — -5 and 200 persisted.
  it("refuses years of experience outside 0–80", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.change(screen.getByTestId("skill-field-years-experience"), { target: { value: "-5" } });
    fireEvent.click(screen.getByTestId("skill-entry-save"));

    expect(await screen.findByTestId("skill-entry-validation-error")).toHaveTextContent("0–80");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("cancelling the remove confirmation sends nothing", () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-remove-0"));
    fireEvent.click(screen.getByTestId("skill-entry-remove-cancel"));
    expect(screen.queryByTestId("skill-entry-remove-dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Adversarial pass 2026-08-26 — F1 (blocker): a stale-conflict retry on
  // Remove must never leave the confirm dialog open on the same index.
  it("on a 409 during confirmRemove, closes the confirm dialog, reloads via onProfileUpdated, and shows a list-level stale notice", async () => {
    const current = { updated_at: "t3", profile: { skills: [FULL_SKILL] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-remove-0"));
    fireEvent.click(screen.getByTestId("skill-entry-remove-confirm"));

    await waitFor(() => expect(screen.getByTestId("skills-stale-notice")).toBeInTheDocument());
    expect(screen.queryByTestId("skill-entry-remove-dialog")).not.toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // F1 — the removal is keyed on the entry's id, not on its position.
  it("removes the skill by id even when the list has been reordered since the confirm dialog opened", async () => {
    const other: Skill = { ...FULL_SKILL, id: "s2", name: "Terraform" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { skills: [other] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { rerender } = render(withIntl(
      <SkillsEditor
        entries={[FULL_SKILL, other]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    fireEvent.click(screen.getByTestId("skill-remove-1"));

    rerender(withIntl(
      <SkillsEditor
        entries={[other, FULL_SKILL]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    fireEvent.click(screen.getByTestId("skill-entry-remove-confirm"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([FULL_SKILL]);
  });

  // F1c — the confirm dialog names the entry being removed.
  it("names the entry in the remove confirmation body", () => {
    renderEditor([FULL_SKILL]);
    fireEvent.click(screen.getByTestId("skill-remove-0"));
    expect(screen.getByTestId("skill-entry-remove-dialog").textContent).toContain("Kubernetes");
  });

  // F2 (major) — a failed save leaves focus outside the dialog; Escape must
  // still close it via a document-level listener.
  it("closes the dialog on a document-level Escape after a failed (422) save", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "name must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_SKILL]);

    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.click(screen.getByTestId("skill-entry-save"));
    await screen.findByTestId("skill-entry-dialog-error");

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByTestId("skill-entry-dialog")).not.toBeInTheDocument();
  });

  // F4 — two id-less legacy entries (id: "") must not collide on their React key.
  it("renders two chips for two skills sharing an empty id, without a key warning", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const a: Skill = { ...FULL_SKILL, id: "", name: "Alpha Skill" };
    const b: Skill = { ...FULL_SKILL, id: "", name: "Beta Skill" };
    renderEditor([a, b]);

    expect(screen.getByTestId("skill-edit-0")).toBeInTheDocument();
    expect(screen.getByTestId("skill-edit-1")).toBeInTheDocument();
    const keyWarning = errorSpy.mock.calls.some((call) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
    expect(keyWarning).toBe(false);
    errorSpy.mockRestore();
  });
});
