// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/** 789:265 — a position section is edited one bullet per field. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { SectionEditor } from "../SectionEditor";
import { saveScopeStorageKey } from "../SaveScopePrompt";

const POSITION = {
  section_id: "position::p1",
  label: "Lead — Acme",
  content: "Led 38 people\nCut scrap by 12 %\nIntroduced MES",
  has_override: false,
  gaps: [],
};

function setup(section = POSITION) {
  // skip the save-scope prompt: this file tests the bullet editor, not the prompt
  sessionStorage.setItem(saveScopeStorageKey(), "cv");
  const onUnsavedChange = vi.fn();
  const onSaved = vi.fn();
  const onDiscarded = vi.fn();
  const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ html: "<p/>", overrides_applied: [], resolved_gaps: [] }),
  } as Response);
  render(
    withIntl(
      <SectionEditor
        cvId="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        section={section}
        onSaved={onSaved}
        onUnsavedChange={onUnsavedChange}
        onDiscarded={onDiscarded}
      />,
    ),
  );
  return { onUnsavedChange, onSaved, onDiscarded, fetchMock };
}

const patchBody = (fetchMock: ReturnType<typeof vi.spyOn>) =>
  JSON.parse((fetchMock.mock.calls.find((c) => (c[1] as RequestInit)?.method === "PATCH")![1] as RequestInit).body as string);

describe("SectionEditor — position bullets (789:265)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("renders one field per bullet and no single textarea", () => {
    setup();
    expect(screen.queryByTestId("section-textarea")).toBeNull();
    expect((screen.getByTestId("section-bullet-0") as HTMLTextAreaElement).value).toBe("Led 38 people");
    expect((screen.getByTestId("section-bullet-2") as HTMLTextAreaElement).value).toBe("Introduced MES");
    expect(screen.queryByTestId("section-bullet-3")).toBeNull();
  });

  it("editing bullet 2 marks unsaved and PATCHes the lines joined by newline", async () => {
    const { onUnsavedChange, fetchMock } = setup();
    fireEvent.change(screen.getByTestId("section-bullet-1"), { target: { value: "Cut scrap by 20 %" } });
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByTestId("section-save"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(patchBody(fetchMock).content).toBe("Led 38 people\nCut scrap by 20 %\nIntroduced MES");
  });

  it("add appends an empty bullet and focuses it", () => {
    setup();
    fireEvent.click(screen.getByTestId("section-bullet-add"));
    const added = screen.getByTestId("section-bullet-3") as HTMLTextAreaElement;
    expect(added.value).toBe("");
    expect(document.activeElement).toBe(added);
  });

  it("remove drops that bullet", () => {
    setup();
    fireEvent.click(screen.getByTestId("section-bullet-remove-0"));
    expect((screen.getByTestId("section-bullet-0") as HTMLTextAreaElement).value).toBe("Cut scrap by 12 %");
    expect(screen.queryByTestId("section-bullet-2")).toBeNull();
  });

  it("a typed newline becomes a space, not a hidden extra line", () => {
    setup();
    fireEvent.change(screen.getByTestId("section-bullet-0"), { target: { value: "Led\n38 people" } });
    expect((screen.getByTestId("section-bullet-0") as HTMLTextAreaElement).value).toBe("Led 38 people");
    expect(screen.queryByTestId("section-bullet-3")).toBeNull();
  });

  it("empty bullets are dropped on save", async () => {
    const { fetchMock, onSaved } = setup();
    fireEvent.click(screen.getByTestId("section-bullet-add"));
    fireEvent.change(screen.getByTestId("section-bullet-1"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("section-save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const content = patchBody(fetchMock).content;
    expect(content).toBe("Led 38 people\nIntroduced MES");
    expect(onSaved.mock.calls[0][1]).toBe(content);
  });

  it("discard resets the draft and calls onDiscarded", () => {
    const { onDiscarded } = setup();
    fireEvent.change(screen.getByTestId("section-bullet-0"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("section-cancel"));
    expect((screen.getByTestId("section-bullet-0") as HTMLTextAreaElement).value).toBe("Led 38 people");
    expect(onDiscarded).toHaveBeenCalledTimes(1);
  });

  it("introduction keeps the single textarea", () => {
    setup({ ...POSITION, section_id: "introduction", content: "Hello\nWorld" });
    expect(screen.getByTestId("section-textarea")).toBeTruthy();
    expect(screen.queryByTestId("section-bullet-0")).toBeNull();
  });
});
