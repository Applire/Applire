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

import { createRef } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { SectionEditor, type SectionEditorHandle } from "../SectionEditor";

const MOCK_SECTION = {
  section_id: "introduction",
  label: "Introduction",
  content: "Original content",
  has_override: false,
  gaps: [{ id: "Python", label: "Python" }],
};

const BASE_PROPS = {
  cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  section: MOCK_SECTION,
  onSaved: vi.fn(),
  onUnsavedChange: vi.fn(),
};

describe("SectionEditor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("pre-fills textarea with section content", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    const textarea = screen.getByTestId("section-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Original content");
  });

  it("disables Save and Cancel when content is unchanged", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    expect((screen.getByTestId("section-save") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("section-cancel") as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables Save and Cancel when content changes", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "New content" },
    });
    expect((screen.getByTestId("section-save") as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByTestId("section-cancel") as HTMLButtonElement).disabled).toBe(false);
  });

  it("Cancel reverts textarea to original content", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "New content" },
    });
    fireEvent.click(screen.getByTestId("section-cancel"));
    const textarea = screen.getByTestId("section-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Original content");
  });

  it("Save shows scope prompt when no remembered choice", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "New content" },
    });
    fireEvent.click(screen.getByTestId("section-save"));
    expect(screen.getByTestId("save-cv-only-btn")).toBeTruthy();
    expect(screen.getByTestId("save-to-profile-btn")).toBeTruthy();
  });

  it("injectSuggestion(text, false) loads the text into the editor, unsaved", () => {
    const ref = createRef<SectionEditorHandle>();
    const onUnsavedChange = vi.fn();
    render(withIntl(<SectionEditor {...BASE_PROPS} ref={ref} onUnsavedChange={onUnsavedChange} />));
    act(() => ref.current!.injectSuggestion("Kaile rewrite", false));
    const textarea = screen.getByTestId("section-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Kaile rewrite");
    expect((screen.getByTestId("section-save") as HTMLButtonElement).disabled).toBe(false);
    expect(onUnsavedChange).toHaveBeenLastCalledWith(true);
  });

  it("injectSuggestion(text, true) saves immediately with the injected text", async () => {
    const mockFetch = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ html: "<html/>", overrides_applied: [], resolved_gaps: [] }),
    } as Response);
    sessionStorage.setItem("finetune_save_scope", "cv");
    const ref = createRef<SectionEditorHandle>();
    render(withIntl(<SectionEditor {...BASE_PROPS} ref={ref} />));
    act(() => ref.current!.injectSuggestion("Applied text", true));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/sections/introduction"),
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ content: "Applied text", save_to_profile: false }),
        })
      );
    });
  });

  it("Save calls PATCH with correct payload after scope selection", async () => {
    const mockFetch = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        html: "<html>updated</html>",
        overrides_applied: ["introduction"],
        resolved_gaps: [],
      }),
    } as Response);

    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "New content" },
    });
    fireEvent.click(screen.getByTestId("section-save"));
    fireEvent.click(screen.getByTestId("save-cv-only-btn"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/sections/introduction"),
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ content: "New content", save_to_profile: false }),
        })
      );
    });
  });

  it("shows error message when PATCH fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    sessionStorage.setItem("finetune_save_scope", "cv");

    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "New content" },
    });
    fireEvent.click(screen.getByTestId("section-save"));

    await screen.findByText("Speichern fehlgeschlagen. Bitte erneut versuchen.");
  });

  it("renders gap hints", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    expect(screen.getByText("Python")).toBeTruthy();
    expect(screen.getByTestId("write-myself-btn")).toBeTruthy();
    expect(screen.getByTestId("kaile-help-btn")).toBeTruthy();
  });

  it("'Kaile hilft' button is enabled", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    expect((screen.getByTestId("kaile-help-btn") as HTMLButtonElement).disabled).toBe(false);
  });

  it("dismissing a gap removes it from the list", () => {
    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    fireEvent.click(screen.getByTestId("write-myself-btn"));
    expect(screen.queryByText("Python")).toBeNull();
  });

  it("Save invokes onSaved with html, content, and resolvedGaps", async () => {
    const onSaved = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        html: "<html>updated</html>",
        overrides_applied: ["introduction"],
        resolved_gaps: ["Python"],
      }),
    } as Response);

    sessionStorage.setItem("finetune_save_scope", "cv");

    render(withIntl(<SectionEditor {...BASE_PROPS} onSaved={onSaved} />));
    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "Updated content" },
    });
    fireEvent.click(screen.getByTestId("section-save"));

    await waitFor(() => {
      expect(onSaved).toHaveBeenCalledWith("<html>updated</html>", "Updated content", ["Python"]);
    });
  });

  it("resolved_gaps from save removes gap from visible list", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        html: "<html/>",
        overrides_applied: ["introduction"],
        resolved_gaps: ["Python"],
      }),
    } as Response);
    sessionStorage.setItem("finetune_save_scope", "cv");

    render(withIntl(<SectionEditor {...BASE_PROPS} />));
    expect(screen.queryAllByTestId("write-myself-btn").length).toBeGreaterThan(0);

    fireEvent.change(screen.getByTestId("section-textarea"), {
      target: { value: "Python developer" },
    });
    fireEvent.click(screen.getByTestId("section-save"));

    await waitFor(() => {
      expect(screen.queryAllByTestId("write-myself-btn").length).toBe(0);
    });
  });
});
