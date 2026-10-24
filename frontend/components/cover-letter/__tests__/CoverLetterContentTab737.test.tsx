// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * #737 (WP-E, D2) — *Abbrechen* on the letter body returns to the LAST SAVED
 * body. It used to reset to the generated paragraphs, so Cancel → a keystroke →
 * Save wrote back a passage *Nimm es für mich heraus* had removed.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { createRef } from "react";
import { act } from "@testing-library/react";
import { CoverLetterContentTab, type CoverLetterContentTabHandle } from "../CoverLetterContentTab";

const GENERATED = { body: { paragraphs: ["I built European e-commerce platforms.", "I led settlement."] } };
const TAKEN_OUT = "I built platforms.\n\nI led settlement.";

function setup(onDirtyChange = vi.fn()) {
  const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({ ok: true, json: () => Promise.resolve({}) } as Response);
  render(
    withIntl(
      <CoverLetterContentTab
        coverLetterId="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        letterData={GENERATED}
        initialBody={TAKEN_OUT}
        onSectionSaved={vi.fn()}
        onDirtyChange={onDirtyChange}
      />,
    ),
  );
  return { fetchSpy, onDirtyChange };
}

describe("CoverLetterContentTab — #737", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Cancel returns to the effective body, never the generated one (D2)", async () => {
    const { fetchSpy } = setup();
    const textarea = screen.getByTestId("cl-body-textarea") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "draft" } });
    fireEvent.click(screen.getByTestId("cl-cancel-body-btn"));
    expect(textarea.value).toBe(TAKEN_OUT);
    // …and a keystroke + save afterwards cannot bring the removed wording back.
    fireEvent.change(textarea, { target: { value: `${TAKEN_OUT} ` } });
    fireEvent.click(screen.getByTestId("cl-save-body-btn"));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const body = JSON.parse(String(fetchSpy.mock.calls[0][1]!.body));
    expect(body.content).not.toContain("European e-commerce");
  });

  it("after a save, Cancel returns to the saved text", async () => {
    setup();
    const textarea = screen.getByTestId("cl-body-textarea") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "saved version" } });
    fireEvent.click(screen.getByTestId("cl-save-body-btn"));
    await waitFor(() => expect(screen.queryByTestId("cl-save-body-btn")).toBeNull());
    fireEvent.change(textarea, { target: { value: "another draft" } });
    fireEvent.click(screen.getByTestId("cl-cancel-body-btn"));
    expect(textarea.value).toBe("saved version");
  });

  it("reports the draft for the unsaved-change dialog", () => {
    const { onDirtyChange } = setup();
    fireEvent.change(screen.getByTestId("cl-body-textarea"), { target: { value: "draft" } });
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByTestId("cl-cancel-body-btn"));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("states where header, recipient and closing come from", () => {
    setup();
    expect(screen.getByTestId("cl-fixed-line").textContent).toBe(
      "Header, recipient and closing come from your profile and the job posting.",
    );
  });

  it("#789:189 — the handle's saveBody persists a dirty draft, and is a no-op true when clean", async () => {
    const ref = createRef<CoverLetterContentTabHandle>();
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({ ok: true, json: () => Promise.resolve({}) } as Response);
    render(
      withIntl(
        <CoverLetterContentTab ref={ref} coverLetterId="c1" letterData={GENERATED} initialBody={TAKEN_OUT} onSectionSaved={vi.fn()} />,
      ),
    );
    await expect(ref.current!.saveBody()).resolves.toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("cl-body-textarea"), { target: { value: "edited" } });
    let ok = false;
    await act(async () => { ok = await ref.current!.saveBody(); });
    expect(ok).toBe(true);
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]!.body))).toEqual({ section: "body", content: "edited" });
  });

  it("#789:189 — saveBody answers false when the save fails", async () => {
    const ref = createRef<CoverLetterContentTabHandle>();
    vi.spyOn(global, "fetch").mockResolvedValue({ ok: false, status: 500 } as Response);
    render(
      withIntl(
        <CoverLetterContentTab ref={ref} coverLetterId="c1" letterData={GENERATED} initialBody={TAKEN_OUT} onSectionSaved={vi.fn()} />,
      ),
    );
    fireEvent.change(screen.getByTestId("cl-body-textarea"), { target: { value: "edited" } });
    let ok = true;
    await act(async () => { ok = await ref.current!.saveBody(); });
    expect(ok).toBe(false);
  });
});
