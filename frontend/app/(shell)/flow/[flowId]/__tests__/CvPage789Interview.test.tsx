// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * Hazelnut build 7, WP-F — 789:254 (ADR-016 amended 2026-11-07, F-4 = A built):
 * after the interview, "Lebenslauf erstellen" lands on the CV page with a CV that
 * predates facts the interview added. The page starts the new version (same
 * template + page target), through the #737 dialog when the CV carries own edits,
 * and shows the "älter als dein Interview" strip while that CV is on screen.
 * 789:252: the phone's document switch is mounted above the preview.
 */
import { Suspense, forwardRef, useImperativeHandle } from "react";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";

const markEdited = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/cv/CVDocument", () => ({
  CVDocument: forwardRef(function Stub(_p: unknown, ref) {
    useImperativeHandle(ref, () => ({ refresh: () => {} }));
    return <div data-testid="cv-document" />;
  }),
}));
vi.mock("@/components/cv/ContentTab", () => ({
  ContentTab: forwardRef(function Stub({ onSectionSave }: { onSectionSave?: () => void }, ref) {
    useImperativeHandle(ref, () => ({ saveOpenSection: async () => true, discardOpenSection: () => {} }));
    return <button data-testid="stub-save" onClick={() => onSectionSave?.()} />;
  }),
}));
vi.mock("@/components/cv/DesignTab", () => ({ DesignTab: () => <div /> }));
vi.mock("@/components/cv/CVActionsTab", () => ({ CVActionsTab: () => <div /> }));
vi.mock("@/components/cv/WhatNext", () => ({ WhatNext: () => <div /> }));
vi.mock("@/components/cv/MobileCommandBar", () => ({ MobileCommandBar: () => <div /> }));
vi.mock("@/components/cover-letter/GenerateCoverLetterModal", () => ({ GenerateCoverLetterModal: () => <div /> }));
vi.mock("@/components/document/ReviewSurface", () => ({
  ReviewSurface: ({ onEditFinding }: { onEditFinding?: (r: unknown) => void }) => (
    <div data-testid="review-stub">
      <button
        data-testid="stub-edit-unreviewed"
        onClick={() =>
          onEditFinding?.({ findingKey: "send:terminal-review", label: "Wahrheitsprüfung", targets: [{ form: "Neu." }], placeIndex: 0, sectionId: "skills" })
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

import CVPage from "../cv/page";

type Calls = { url: string; init?: RequestInit }[];

function mockFetch(opts: { step: string; predates: boolean; updated?: boolean; edited?: boolean }): Calls {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });
    if (url.includes("/state")) {
      return {
        ok: true,
        json: async () => ({
          job_id: "job-123",
          current_step: opts.step,
          application_id: "app-1",
          job_summary: { role_title: "Backend Engineer" },
          cv_summary: {
            cv_id: "cv-old",
            pdf_url: "/x.pdf",
            expires_at: "2099-01-01T00:00:00Z",
            predates_interview: opts.predates,
            profile_updated_since: opts.updated ?? opts.predates,
          },
        }),
      } as Response;
    }
    if (url.includes("/sections")) {
      return {
        ok: true,
        json: async () => ({
          sections: [
            { section_id: "introduction", label: "Introduction", content: "x", has_override: !!opts.edited, gaps: [] },
          ],
          general_gaps: [],
        }),
      } as Response;
    }
    if (url.includes("/cv/cv-old/status")) {
      return { ok: true, json: async () => ({ template: "modern_swiss", target_pages: 3, document_language: "en" }) } as Response;
    }
    if (url.endsWith("/api/cv/generate")) {
      return { ok: true, json: async () => ({ cv_id: "cv-new", status: "pending", expires_at: "2099-01-01T00:00:00Z" }) } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  return calls;
}

const generateCalls = (calls: Calls) => calls.filter((c) => c.url.endsWith("/api/cv/generate"));
const advanceBodies = (calls: Calls) =>
  calls.filter((c) => c.url.includes("/advance")).map((c) => JSON.parse(String(c.init?.body ?? "{}")));

async function mount() {
  await act(async () => {
    render(
      withIntl(
        <Suspense fallback={null}>
          <CVPage params={Promise.resolve({ flowId: "f1" })} />
        </Suspense>,
        "de",
      ),
    );
  });
}

describe("CVPage — 789:254 the post-interview version", () => {
  beforeEach(() => {
    markEdited.mockReset();
    window.sessionStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("at cv_generation with a CV that predates the interview, the new version starts in the old CV's template and page target", async () => {
    const calls = mockFetch({ step: "cv_generation", predates: true });
    await mount();
    await waitFor(() => expect(generateCalls(calls)).toHaveLength(1));
    const body = JSON.parse(String(generateCalls(calls)[0].init?.body));
    expect(body).toEqual({ job_id: "job-123", template: "modern_swiss", target_pages: 3 });
    // The generating view names why this version is written.
    expect(await screen.findByText("Neue Fassung mit deinen Antworten aus dem Interview")).toBeInTheDocument();
  });

  it("a CV with own section edits asks the #737 question first — nothing is generated until confirmed", async () => {
    const calls = mockFetch({ step: "cv_generation", predates: true, edited: true });
    await mount();
    // The dialog is open and names the edited section; no generation yet.
    expect(await screen.findByText(/Nicht übernommen wird Deine Bearbeitung/)).toBeInTheDocument();
    expect(generateCalls(calls)).toHaveLength(0);
    fireEvent.click(screen.getByTestId("edit-new-version-confirm"));
    await waitFor(() => expect(generateCalls(calls)).toHaveLength(1));
  });

  it("on a complete flow the old CV stays, with the strip and its one action — no automatic generation", async () => {
    const calls = mockFetch({ step: "complete", predates: true });
    await mount();
    const strip = (await screen.findAllByTestId("cv-profile-updated"))[0];
    expect(strip.textContent).toContain("Dein Profil wurde seit der Erstellung dieses Lebenslaufs aktualisiert");
    expect(generateCalls(calls)).toHaveLength(0);
    fireEvent.click(screen.getAllByTestId("cv-profile-updated-action")[0]);
    // The one action is the existing new-version path (#737 dialog first).
    fireEvent.click(await screen.findByTestId("edit-new-version-confirm"));
    await waitFor(() => expect(generateCalls(calls)).toHaveLength(1));
  });

  it("a current CV at cv_generation is not regenerated; the page finishes the flow instead", async () => {
    const calls = mockFetch({ step: "cv_generation", predates: false });
    await mount();
    await waitFor(() => expect(advanceBodies(calls)).toContainEqual({ step: "complete", artifact_id: "cv-old" }));
    expect(generateCalls(calls)).toHaveLength(0);
    expect(screen.queryByTestId("cv-profile-updated")).toBeNull();
  });

  it("F-2 (2): a profile change from another door (import, manual edit) shows the strip and starts nothing", async () => {
    const calls = mockFetch({ step: "cv_generation", predates: false, updated: true });
    await mount();
    expect((await screen.findAllByTestId("cv-profile-updated"))[0]).toBeInTheDocument();
    // The auto-start stays interview-only (F-4): the flow is finished, no generation.
    await waitFor(() => expect(advanceBodies(calls)).toContainEqual({ step: "complete", artifact_id: "cv-old" }));
    expect(generateCalls(calls)).toHaveLength(0);
  });

  it("789:252 — the phone switch to the letter is mounted above the preview", async () => {
    mockFetch({ step: "complete", predates: false });
    await mount();
    const link = (await screen.findAllByTestId("document-nav-mobile-cover-letter"))[0];
    expect(link.getAttribute("href")).toBe("/flow/f1/cover-letter");
  });
});
