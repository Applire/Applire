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
  GenerationProgress: ({ cvId }: { cvId: string }) => <div data-testid="generation-progress" data-cv={cvId} />,
}));
vi.mock("@/components/cv/CVDocument", async () => {
  const { forwardRef } = await import("react");
  return { CVDocument: forwardRef<unknown, { cvId: string }>(({ cvId }, _ref) => <div data-testid="cv-on-screen" data-cv={cvId} />) };
});

import CVPage from "../cv/page";

const SUMMARY = { cv_id: "cv-ready", expires_at: "2099-01-01T00:00:00Z", sections: [] };

function mockFetch(
  newestStatus: string,
  list?: Array<{ cv_id: string; status: string }>,
  cvSummary: typeof SUMMARY | null = null,
) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/state")) {
      return { ok: true, json: async () => ({ job_id: "job-1", current_step: cvSummary ? "complete" : "cv_generation", cv_summary: cvSummary }) } as Response;
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
  // MD-G36 item 5: the newest attempt for the job decides, from both entry states.
  const FAILED_OVER_READY = [{ cv_id: "cv-new", status: "failed" }, { cv_id: "cv-ready", status: "ready" }];

  it("item 5, no cv_summary: the older ready version is on screen and the failure is said", async () => {
    mockFetch("failed", FAILED_OVER_READY);
    await mount();
    expect((await screen.findByTestId("cv-on-screen")).dataset.cv).toBe("cv-ready");
    expect(screen.getByTestId("cv-newer-attempt-failed")).toHaveTextContent("Generierung fehlgeschlagen.");
    expect(screen.getByTestId("cv-newer-attempt-retry")).toHaveTextContent("Erneut versuchen");
    expect(screen.queryByTestId("generation-progress")).toBeNull();
    const advanced = vi.mocked(globalThis.fetch).mock.calls
      .filter(([u]) => String(u).includes("/advance"))
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(advanced).toContainEqual({ step: "complete", artifact_id: "cv-ready" });
  });

  it("item 5, with cv_summary: a newer failed attempt is said above the version on screen", async () => {
    mockFetch("failed", FAILED_OVER_READY, SUMMARY);
    await mount();
    expect((await screen.findByTestId("cv-on-screen")).dataset.cv).toBe("cv-ready");
    expect(await screen.findByTestId("cv-newer-attempt-failed")).toHaveTextContent("Generierung fehlgeschlagen.");
  });

  it("item 5, with cv_summary: no notice when the version on screen is the newest", async () => {
    mockFetch("ready", [{ cv_id: "cv-ready", status: "ready" }, { cv_id: "cv-older", status: "failed" }], SUMMARY);
    await mount();
    expect((await screen.findByTestId("cv-on-screen")).dataset.cv).toBe("cv-ready");
    await act(async () => {});
    expect(screen.queryByTestId("cv-newer-attempt-failed")).toBeNull();
  });

  it("a failed attempt with no ready version shows the failed attempt, no notice", async () => {
    mockFetch("failed");
    await mount();
    await screen.findByTestId("generation-progress");
    expect(screen.queryByTestId("cv-newer-attempt-failed")).toBeNull();
  });
});
