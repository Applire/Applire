// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * 786:165 (E-12 walk, founder 2026-10-22): the letter page's Aktionen offers the
 * same application-scoped PinnedFactsPanel as the CV's Aktionen (principle 11
 * parity) — fed with the application, this letter AND the application's CV, so the
 * fate markers measure both documents.
 */
import { Suspense } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/components/cover-letter/CoverLetterDocument", () => ({
  CoverLetterDocument: () => <div data-testid="cl-document" />,
}));
vi.mock("@/components/cover-letter/CoverLetterContentTab", () => ({
  CoverLetterContentTab: () => <div data-testid="cl-content-tab" />,
}));
// The actions tab renders its pins slot, so the page's slot content is visible.
vi.mock("@/components/cover-letter/CoverLetterActionsTab", () => ({
  CoverLetterActionsTab: ({ pins }: { pins?: React.ReactNode }) => (
    <div data-testid="cl-actions-tab">{pins}</div>
  ),
}));
vi.mock("@/components/cover-letter/GenerateCoverLetterModal", () => ({
  GenerateCoverLetterModal: () => <div data-testid="cl-modal" />,
}));
vi.mock("@/components/pins/PinnedFactsPanel", () => ({
  PinnedFactsPanel: (props: {
    applicationId: string;
    cvId?: string | null;
    coverLetterId?: string | null;
  }) => (
    <div
      data-testid="pinned-facts-panel-stub"
      data-application={props.applicationId}
      data-cv={props.cvId ?? ""}
      data-letter={props.coverLetterId ?? ""}
    />
  ),
}));

import CoverLetterPage from "../page";

function mockFetch(flowState: Record<string, unknown>) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/state")) return { ok: true, json: async () => flowState } as Response;
    if (url.includes("/status")) {
      return {
        ok: true,
        json: async () => ({ status: "ready", letter_data: { header: { name: "Max" } } }),
      } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
}

async function openActions() {
  const tabs = await screen.findAllByTestId("sidebar-tab-actions", {}, { timeout: 8000 });
  await act(async () => { fireEvent.click(tabs[0]); });
}

function renderPage() {
  return render(
    withIntl(
      <Suspense fallback={null}>
        <CoverLetterPage params={Promise.resolve({ flowId: "flow-1" })} />
      </Suspense>,
    ),
  );
}

const BASE = {
  cover_letter_summary: { cover_letter_id: "cl-1", status: "ready", template: "classic_german" },
  job_id: "job-1",
  job_summary: { role_title: "Leiter Operations" },
};

describe("786:165 — the letter's Aktionen offers the pin panel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.sessionStorage.clear();
  });

  it("mounts PinnedFactsPanel with the application, this letter and the application's CV", { timeout: 15000 }, async () => {
    mockFetch({ ...BASE, application_id: "app-1", cv_summary: { cv_id: "cv-1", expires_at: "2027-01-01" } });
    await act(async () => { renderPage(); });
    await openActions();
    const panels = await screen.findAllByTestId("pinned-facts-panel-stub", {}, { timeout: 5000 });
    expect(panels.length).toBeGreaterThan(0);
    const panel = panels[0];
    expect(panel.getAttribute("data-application")).toBe("app-1");
    expect(panel.getAttribute("data-letter")).toBe("cl-1");
    expect(panel.getAttribute("data-cv")).toBe("cv-1");
  });

  it("a letter with no application (agent-authored legacy row) shows no pin panel", { timeout: 15000 }, async () => {
    mockFetch({ ...BASE, application_id: null });
    await act(async () => { renderPage(); });
    await openActions();
    await screen.findAllByTestId("cl-actions-tab", {}, { timeout: 5000 });
    expect(screen.queryByTestId("pinned-facts-panel-stub")).toBeNull();
  });
});
