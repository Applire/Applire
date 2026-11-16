// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Q3-4 / Q8-3 (Sorbet wave 2b) — a bullet an interview answer replaced shows
 * "Ersetzt: „<alt>“ [Rückgängig] [Ausblenden]"; undo and dismiss post to their
 * doors and hand the returned profile on.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkExperienceEditor } from "../WorkExperienceEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { WorkEntry } from "@/lib/profile-entries";

const OLD = "Fachliche Anleitung von zwei Werkstudierenden.";
const NEW = "Seit zwei Jahren fachliche Anleitung von zwei Werkstudierenden und einer Junior-Controllerin.";

const ENTRY: WorkEntry = {
  id: "w-ctrl",
  company: "Schwarzwald Präzision GmbH",
  role: "Financial Controller",
  responsibilities: [NEW, "Monatsabschluss nach HGB."],
  achievements: [],
  replaced_bullets: [{ text: NEW, replaced: OLD, field: "responsibilities" }],
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: WorkEntry[], locale: "de" | "en" = "de") {
  const onProfileUpdated = vi.fn();
  render(
    withIntl(
      <WorkExperienceEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-11-16T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
      locale,
    ),
  );
  return { onProfileUpdated };
}

describe("replaced bullet line (Q3-4 / Q8-3)", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  it("shows the German line under the new bullet only", () => {
    renderEditor([ENTRY]);
    const lines = screen.getAllByTestId("replaced-bullet-line");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toContain(`Ersetzt: „${OLD}“`);
    expect(lines[0].textContent).toContain("Rückgängig");
    expect(lines[0].textContent).toContain("Ausblenden");
    expect(lines[0].closest("li")?.textContent).toContain(NEW);
  });

  it("renders the English form", () => {
    renderEditor([ENTRY], "en");
    const line = screen.getByTestId("replaced-bullet-line");
    expect(line.textContent).toContain(`Replaced: “${OLD}”`);
    expect(line.textContent).toContain("Undo");
    expect(line.textContent).toContain("Dismiss");
  });

  it("shows nothing for an entry without a record", () => {
    renderEditor([{ ...ENTRY, replaced_bullets: undefined }]);
    expect(screen.queryByTestId("replaced-bullet-line")).toBeNull();
  });

  it.each([
    ["replaced-bullet-undo", "/api/profile/replaced-bullets/undo"],
    ["replaced-bullet-dismiss", "/api/profile/replaced-bullets/dismiss"],
  ])("%s posts the record and hands the profile on", async (testId, path) => {
    const profile = { profile: {}, updated_at: "x" };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse(200, profile));
    const { onProfileUpdated } = renderEditor([ENTRY]);
    fireEvent.click(screen.getByTestId(testId));
    await waitFor(() => expect(onProfileUpdated).toHaveBeenCalledWith(profile));
    const [url, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain(path);
    expect(JSON.parse(init.body)).toEqual({ entry_id: "w-ctrl", text: NEW });
  });

  it("says so when the door fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse(404, {}));
    const { onProfileUpdated } = renderEditor([ENTRY]);
    fireEvent.click(screen.getByTestId("replaced-bullet-undo"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das hat nicht geklappt");
    expect(onProfileUpdated).not.toHaveBeenCalled();
  });
});
