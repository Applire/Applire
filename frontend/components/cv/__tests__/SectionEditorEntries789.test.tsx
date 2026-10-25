// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
// L-3 = A (789:193): certification / education entries edit as single-line
// fields, and an entry can be taken off this CV and put back.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { SectionEditor } from "../SectionEditor";

const CERT = {
  section_id: "certification::0",
  label: "REFA-Grundschein",
  content: "REFA-Grundschein ·  · 2013",
  has_override: false,
  gaps: [],
};

function props(section = CERT) {
  return {
    cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    section,
    onSaved: vi.fn(),
    onUnsavedChange: vi.fn(),
  };
}

function okFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ html: "<p/>", overrides_applied: [], resolved_gaps: [] }), { status: 200 }),
  );
}

describe("SectionEditor — entry sections (789:193)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("shows one single-line field per entry field, never a textarea", () => {
    render(withIntl(<SectionEditor {...props()} />));
    expect(screen.queryByTestId("section-textarea")).toBeNull();
    expect((screen.getByTestId("section-entry-name") as HTMLInputElement).value).toBe("REFA-Grundschein");
    expect((screen.getByTestId("section-entry-issuing_organization") as HTMLInputElement).value).toBe("");
    expect((screen.getByTestId("section-entry-date_obtained") as HTMLInputElement).value).toBe("2013");
  });

  it("saves an edited field as the positional entry line", async () => {
    const fetchSpy = okFetch();
    render(withIntl(<SectionEditor {...props()} />));
    fireEvent.change(screen.getByTestId("section-entry-issuing_organization"), { target: { value: "REFA" } });
    fireEvent.click(screen.getByTestId("section-save"));
    // the scope prompt may ask first; pick "only this CV" when it does
    const onlyCv = screen.queryByTestId("save-cv-only-btn");
    if (onlyCv) fireEvent.click(onlyCv);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.content).toBe("REFA-Grundschein · REFA · 2013");
    expect(String(fetchSpy.mock.calls[0][0])).toContain("/sections/certification::0");
  });

  it("takes the entry off this CV (document only) and puts it back", async () => {
    const fetchSpy = okFetch();
    render(withIntl(<SectionEditor {...props()} />));
    fireEvent.click(screen.getByTestId("section-entry-remove"));
    await waitFor(() => expect(screen.getByTestId("section-entry-removed")).toBeTruthy());
    let body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ content: "", save_to_profile: false });
    fireEvent.click(screen.getByTestId("section-entry-restore"));
    await waitFor(() => expect(screen.getByTestId("section-entry-fields")).toBeTruthy());
    body = JSON.parse((fetchSpy.mock.calls[1][1] as RequestInit).body as string);
    expect(body).toEqual({ content: "REFA-Grundschein ·  · 2013", save_to_profile: false });
  });

  it("keeps the textarea for prose sections", () => {
    render(withIntl(<SectionEditor {...props({ ...CERT, section_id: "skills", content: "Lean" })} />));
    expect(screen.getByTestId("section-textarea")).toBeTruthy();
    expect(screen.queryByTestId("section-entry-fields")).toBeNull();
  });
});
