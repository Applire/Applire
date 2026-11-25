// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * DH item 6 (Sorbet invite-gate delivery run, 2026-11-25): a backend stop marked the
 * running render failed; a RELOAD of the CV page showed the template picker with no
 * trace of the failed attempt (the page left open said "Generierung fehlgeschlagen").
 */
import { Suspense } from "react";
import { render, screen, act } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));
vi.mock("@/components/cv/TemplateSelector", () => ({ TemplateSelector: () => <div data-testid="template-picker" /> }));
vi.mock("@/components/cv/GenerationProgress", () => ({
  GenerationProgress: ({ cvId, onOpenEarlier }: { cvId: string; onOpenEarlier?: () => void }) => (
    <div data-testid="generation-progress" data-cv={cvId}>
      {onOpenEarlier && <button data-testid="open-earlier-version" onClick={onOpenEarlier} />}
    </div>
  ),
}));

import CVPage from "../cv/page";

function mockFetch(newestStatus: string, list?: Array<{ cv_id: string; status: string }>) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/state")) {
      return { ok: true, json: async () => ({ job_id: "job-1", current_step: "cv_generation", cv_summary: null }) } as Response;
    }
    if (url.includes("/api/cv?job_id=")) {
      return { ok: true, json: async () => list ?? [{ cv_id: "cv-new", status: newestStatus }, { cv_id: "cv-old", status: "failed" }] } as Response;
    }
    if (url.includes("/api/profile")) {
      return { ok: true, json: async () => ({ profile: { personal_info: { photo_url: "/p.png" } } }) } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
}

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

describe("CVPage — reload after a failed render", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the failed attempt (its progress card), not a bare template picker", async () => {
    mockFetch("failed");
    await mount();
    const card = await screen.findByTestId("generation-progress");
    expect(card.dataset.cv).toBe("cv-new");
    expect(screen.queryByTestId("template-picker")).toBeNull();
  });

  it("a running render still shows its progress", async () => {
    mockFetch("generating");
    await mount();
    expect((await screen.findByTestId("generation-progress")).dataset.cv).toBe("cv-new");
  });
  it("MD-G32 X-8: a failed newest attempt keeps the way to the older ready version", async () => {
    mockFetch("failed", [{ cv_id: "cv-new", status: "failed" }, { cv_id: "cv-ready", status: "ready" }]);
    await mount();
    const card = await screen.findByTestId("generation-progress");
    expect(card.dataset.cv).toBe("cv-new");
    const fetchSpy = vi.mocked(globalThis.fetch);
    await act(async () => screen.getByTestId("open-earlier-version").click());
    const advanced = fetchSpy.mock.calls
      .filter(([u]) => String(u).includes("/advance"))
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(advanced).toContainEqual({ step: "complete", artifact_id: "cv-ready" });
    expect(screen.queryByTestId("generation-progress")).toBeNull();
  });

  it("MD-G32 X-8: a failed attempt with no ready version offers no such way", async () => {
    mockFetch("failed");
    await mount();
    await screen.findByTestId("generation-progress");
    expect(screen.queryByTestId("open-earlier-version")).toBeNull();
  });
});
