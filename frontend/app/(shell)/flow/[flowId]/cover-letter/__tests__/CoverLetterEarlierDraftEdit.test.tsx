// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * Delivery-run defect D1 (letter page): "Selbst bearbeiten" on the "changed after
 * the last check" card opens the body under the earlier-draft strip, not the
 * "not covered by your profile" strip, and a save from it neither re-audits
 * (markEdited) nor claims the row cleared.
 */
import { Suspense } from "react";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";

const markEdited = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
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
vi.mock("@/components/cover-letter/CoverLetterContentTab", () => ({
  CoverLetterContentTab: ({ onSectionSaved }: { onSectionSaved?: () => void }) => (
    <div data-testid="cl-content-tab">
      <button data-testid="stub-save" onClick={() => onSectionSaved?.()} />
    </div>
  ),
}));
vi.mock("@/components/document/ReviewSurface", () => ({
  ReviewSurface: ({ onEditFinding }: { onEditFinding?: (r: unknown) => void }) => (
    <div data-testid="review-stub">
      <button
        data-testid="stub-edit-unreviewed"
        onClick={() =>
          onEditFinding?.({ findingKey: "send:terminal-review", label: "Wahrheitsprüfung", targets: [{ form: "Neu." }], placeIndex: 0, sectionId: "body" })
        }
      />
      <button
        data-testid="stub-edit-finding"
        onClick={() =>
          onEditFinding?.({ findingKey: "ats:keyword:x", label: "Industrie 4.0", targets: [{ form: "MES" }], placeIndex: 0 })
        }
      />
    </div>
  ),
}));
vi.mock("@/lib/api/document-review", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/document-review")>();
  return { ...actual, markEdited };
});

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

async function mount() {
  await act(async () => {
    render(
      withIntl(
        <Suspense fallback={null}>
          <CoverLetterPage params={Promise.resolve({ flowId: "f1" })} />
        </Suspense>,
        "de",
      ),
    );
  });
}

describe("CoverLetterPage — earlier-draft edit context (D1)", () => {
  beforeEach(() => {
    markEdited.mockReset();
    markEdited.mockResolvedValue({});
    mockFetch();
    window.sessionStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("the terminal-review key renders fromUnreviewedBody, never fromFindingBody", async () => {
    await mount();
    fireEvent.click((await screen.findAllByTestId("stub-edit-unreviewed"))[0]);
    const strip = (await screen.findAllByTestId("edit-context-unreviewed"))[0];
    expect(strip.textContent).toContain("nach der letzten Prüfung geändert");
    expect(screen.queryByTestId("edit-context-finding")).toBeNull();
    expect(strip.textContent).not.toContain("nicht durch Dein Profil gedeckt");
  });

  it("an ordinary finding still renders fromFindingBody", async () => {
    await mount();
    fireEvent.click((await screen.findAllByTestId("stub-edit-finding"))[0]);
    expect((await screen.findAllByTestId("edit-context-finding"))[0].textContent).toContain("nicht durch Dein Profil gedeckt");
  });

  it("a save from the earlier-draft context shows the plain receipt and never calls markEdited", async () => {
    await mount();
    fireEvent.click((await screen.findAllByTestId("stub-edit-unreviewed"))[0]);
    fireEvent.click((await screen.findAllByTestId("stub-save"))[0]);
    await waitFor(() => expect(screen.getAllByTestId("edit-receipt-plain").length).toBeGreaterThan(0));
    expect(markEdited).not.toHaveBeenCalled();
  });

  it("a save from an ordinary finding still calls markEdited", async () => {
    await mount();
    fireEvent.click((await screen.findAllByTestId("stub-edit-finding"))[0]);
    fireEvent.click((await screen.findAllByTestId("stub-save"))[0]);
    await waitFor(() => expect(markEdited).toHaveBeenCalledTimes(1));
  });
});
