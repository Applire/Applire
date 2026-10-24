// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * Hazelnut b4 (#789 line 189) — the letter page's unsaved-draft dialog offers
 * Save, like the CV page's section editor guard. Save persists the body, then
 * proceeds with the held-back navigation; Discard and Stay are unchanged.
 */
import { Suspense, forwardRef, useImperativeHandle, useState } from "react";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";

const push = vi.hoisted(() => vi.fn());
const saveBody = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));
vi.mock("@/components/cover-letter/CoverLetterDocument", () => ({
  CoverLetterDocument: () => <div data-testid="cl-document" />,
}));
vi.mock("@/components/cover-letter/CoverLetterActionsTab", () => ({
  CoverLetterActionsTab: () => <div data-testid="cl-actions-tab" />,
}));
vi.mock("@/components/cover-letter/GenerateCoverLetterModal", () => ({
  GenerateCoverLetterModal: () => <div data-testid="cl-modal" />,
}));
// A body editor stub: a button makes the draft dirty; its handle is the seam.
vi.mock("@/components/cover-letter/CoverLetterContentTab", () => ({
  CoverLetterContentTab: forwardRef(function Stub(
    { onDirtyChange }: { onDirtyChange?: (d: boolean) => void },
    ref,
  ) {
    const [dirty, setDirty] = useState(false);
    useImperativeHandle(ref, () => ({
      saveBody: async () => {
        const ok = (await saveBody()) as boolean;
        if (ok) {
          setDirty(false);
          onDirtyChange?.(false);
        }
        return ok;
      },
    }));
    return (
      <div data-testid="cl-content-tab" data-dirty={String(dirty)}>
        <button
          data-testid="make-dirty"
          onClick={() => {
            setDirty(true);
            onDirtyChange?.(true);
          }}
        />
        <a href="/flow/other/cv" data-testid="leave-link">leave</a>
      </div>
    );
  }),
}));

import CoverLetterPage from "../page";

function mockFetch() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/state")) {
      return {
        ok: true,
        json: async () => ({
          cover_letter_summary: { cover_letter_id: "cl-abc", status: "ready", template: "classic_german" },
          job_id: "job-123",
          job_summary: { role_title: "Software Engineer" },
        }),
      } as Response;
    }
    if (url.includes("/status")) {
      return { ok: true, json: async () => ({ status: "ready", letter_data: { header: { name: "Max" } } }) } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
}

async function makeDirtyAndLeave() {
  const editTab = screen.queryByTestId("sidebar-tab-edit");
  if (editTab && !screen.queryByTestId("make-dirty")) fireEvent.click(editTab);
  fireEvent.click((await screen.findAllByTestId("make-dirty"))[0]);
  fireEvent.click((await screen.findAllByTestId("leave-link"))[0]);
}

async function openDialog() {
  await act(async () => {
    render(
      withIntl(
        <Suspense fallback={null}>
          <CoverLetterPage params={Promise.resolve({ flowId: "f1" })} />
        </Suspense>,
      ),
    );
  });
  await makeDirtyAndLeave();
  return screen.findByTestId("edit-unsaved-dialog");
}

describe("CoverLetterPage — unsaved body dialog offers Save (#789:189)", () => {
  beforeEach(() => {
    push.mockReset();
    saveBody.mockReset();
    mockFetch();
    window.sessionStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows Save beside Discard and Stay", async () => {
    await openDialog();
    expect(screen.getByTestId("edit-unsaved-save")).toBeInTheDocument();
    expect(screen.getByTestId("edit-unsaved-discard")).toBeInTheDocument();
    expect(screen.getByTestId("edit-unsaved-stay")).toBeInTheDocument();
  });

  it("Save persists the body, then proceeds with the pending navigation", async () => {
    saveBody.mockResolvedValue(true);
    await openDialog();
    fireEvent.click(screen.getByTestId("edit-unsaved-save"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/flow/other/cv"));
    expect(saveBody).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("edit-unsaved-dialog")).toBeNull();
  });

  it("a failed Save keeps the dialog open, says so, and does not navigate", async () => {
    saveBody.mockResolvedValue(false);
    await openDialog();
    fireEvent.click(screen.getByTestId("edit-unsaved-save"));
    await waitFor(() => expect(screen.getByText(/Saving failed/)).toBeInTheDocument());
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByTestId("edit-unsaved-dialog")).toBeInTheDocument();
  });

  it("Discard navigates without saving; Stay closes without navigating", async () => {
    await openDialog();
    fireEvent.click(screen.getByTestId("edit-unsaved-stay"));
    expect(push).not.toHaveBeenCalled();
    expect(screen.queryByTestId("edit-unsaved-dialog")).toBeNull();

    await makeDirtyAndLeave();
    fireEvent.click(await screen.findByTestId("edit-unsaved-discard"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/flow/other/cv"));
    expect(saveBody).not.toHaveBeenCalled();
  });
});
