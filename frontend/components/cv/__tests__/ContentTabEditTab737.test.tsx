// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * #737 (Strawberry build 2, WP-E) — the section editor on the document pages'
 * *Bearbeiten* tab (`variant="sections"`) no longer repeats the gap handling
 * that *Prüfung* owns (review groups 2/3), says which sections carry the
 * user's own edit, names the open section once, and lets the page's
 * unsaved-change dialog save or drop the draft.
 */
import { createRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ContentTab, type ContentTabHandle } from "../ContentTab";
import { saveScopeStorageKey } from "../SaveScopePrompt";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const SECTIONS = [
  { section_id: "introduction", label: "Introduction", content: "Ops lead", has_override: false, gaps: [] },
  {
    section_id: "position_w1",
    label: "Produktionsleiter — Weberit",
    content: "- Led 38 people",
    has_override: true,
    gaps: [
      { id: "sap-pp", label: "SAP PP", kind: "claimable" as const },
      { id: "ifs", label: "IFS Food", kind: "honest" as const },
    ],
  },
];

const fetchSpy = () =>
  vi.spyOn(global, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (init?.method === "PATCH") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ html: "<html/>", overrides_applied: ["position_w1"], resolved_gaps: [] }),
      } as Response);
    }
    void url;
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ sections: SECTIONS, general_gaps: [] }),
    } as Response);
  });

function renderTab(props: Partial<React.ComponentProps<typeof ContentTab>> = {}, locale: "en" | "de" = "de") {
  const ref = createRef<ContentTabHandle>();
  const onUnsavedChange = vi.fn();
  render(
    withIntl(
      <ContentTab
        ref={ref}
        cvId="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        flowSummary={null}
        onSectionSave={vi.fn()}
        onUnsavedChange={onUnsavedChange}
        variant="sections"
        {...props}
      />,
      locale,
    ),
  );
  return { ref, onUnsavedChange };
}

describe("ContentTab — #737 Edit tab", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists sections without gap counts and marks the edited one", async () => {
    fetchSpy();
    renderTab();
    await screen.findByText("Produktionsleiter — Weberit");
    // No count badge for the two gaps of the edited section…
    expect(screen.queryByText("2")).toBeNull();
    // …but an "edited" tag on exactly that section.
    expect(screen.getByTestId("edit-section-edited-position_w1").textContent).toBe("bearbeitet");
    expect(screen.queryByTestId("edit-section-edited-introduction")).toBeNull();
    expect(screen.getByTestId("edit-sections-intro")).toBeTruthy();
  });

  it("opens a section without the 'related gaps' cards and with its heading once", async () => {
    fetchSpy();
    renderTab();
    fireEvent.click(await screen.findByText("Produktionsleiter — Weberit"));
    await screen.findByTestId("section-textarea");
    expect(screen.queryByText("Verwandte Lücken")).toBeNull();
    expect(screen.queryByTestId("write-myself-btn")).toBeNull();
    expect(screen.queryByTestId("honest-gap-tag")).toBeNull();
    expect(screen.getAllByText("Produktionsleiter — Weberit")).toHaveLength(1);
  });

  it("names the static sections in the UI language inside the editor (no raw 'Introduction')", async () => {
    fetchSpy();
    renderTab();
    fireEvent.click(await screen.findByText("Einleitung"));
    await screen.findByTestId("section-textarea");
    expect(screen.queryByText("Introduction")).toBeNull();
  });

  it("reports the draft with the open section's label", async () => {
    fetchSpy();
    const { onUnsavedChange } = renderTab();
    fireEvent.click(await screen.findByText("Produktionsleiter — Weberit"));
    fireEvent.change(await screen.findByTestId("section-textarea"), { target: { value: "- Led 40 people" } });
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true, "Produktionsleiter — Weberit");
  });

  it("saveOpenSection saves the draft to THIS CV when no scope was remembered", async () => {
    const spy = fetchSpy();
    const { ref, onUnsavedChange } = renderTab();
    fireEvent.click(await screen.findByText("Produktionsleiter — Weberit"));
    fireEvent.change(await screen.findByTestId("section-textarea"), { target: { value: "- Led 40 people" } });
    let ok = false;
    await act(async () => {
      ok = await ref.current!.saveOpenSection();
    });
    expect(ok).toBe(true);
    const patch = spy.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(String(patch[0])).toContain("/sections/position_w1");
    expect(JSON.parse(String(patch[1]!.body))).toEqual({ content: "- Led 40 people", save_to_profile: false });
    // No scope dialog was put in the way.
    expect(screen.queryByText(/Im Master-Profil speichern/)).toBeNull();
    expect(onUnsavedChange).toHaveBeenLastCalledWith(false, "Produktionsleiter — Weberit");
  });

  it("saveOpenSection honours a remembered 'profile' scope", async () => {
    sessionStorage.setItem(saveScopeStorageKey(), "profile");
    const spy = fetchSpy();
    const { ref } = renderTab();
    fireEvent.click(await screen.findByText("Produktionsleiter — Weberit"));
    fireEvent.change(await screen.findByTestId("section-textarea"), { target: { value: "x" } });
    await act(async () => {
      await ref.current!.saveOpenSection();
    });
    const patch = spy.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(JSON.parse(String(patch[1]!.body)).save_to_profile).toBe(true);
  });

  it("saveOpenSection reports a failed save as false", async () => {
    vi.spyOn(global, "fetch").mockImplementation((_input, init) =>
      Promise.resolve(
        init?.method === "PATCH"
          ? ({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)
          : ({ ok: true, json: () => Promise.resolve({ sections: SECTIONS, general_gaps: [] }) } as Response),
      ),
    );
    const { ref } = renderTab();
    fireEvent.click(await screen.findByText("Produktionsleiter — Weberit"));
    fireEvent.change(await screen.findByTestId("section-textarea"), { target: { value: "x" } });
    let ok = true;
    await act(async () => {
      ok = await ref.current!.saveOpenSection();
    });
    expect(ok).toBe(false);
  });

  it("discardOpenSection drops the draft and returns to the list", async () => {
    fetchSpy();
    const { ref, onUnsavedChange } = renderTab();
    fireEvent.click(await screen.findByText("Produktionsleiter — Weberit"));
    fireEvent.change(await screen.findByTestId("section-textarea"), { target: { value: "x" } });
    act(() => ref.current!.discardOpenSection());
    await waitFor(() => expect(screen.queryByTestId("section-textarea")).toBeNull());
    expect(onUnsavedChange).toHaveBeenLastCalledWith(false, null);
  });

  it("variant 'full' (other callers) keeps its gap counts", async () => {
    fetchSpy();
    renderTab({ variant: "full" }, "en");
    await screen.findByText("Produktionsleiter — Weberit");
    expect(screen.getByText("2")).toBeTruthy();
  });
});
