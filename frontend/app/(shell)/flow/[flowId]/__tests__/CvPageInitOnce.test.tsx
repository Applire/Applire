// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * The CV page decides its first phase in an effect. Under React StrictMode (the dev
 * server, which the E2E tiers run on) that effect runs twice; the run whose effect was
 * cleaned up must not set the phase. When it did, it could put the page back to the
 * photo question in the same batch in which the remembered "skip" moved it on — the
 * photo step then rendered nothing and the page stayed empty (PQ, Emma dashboard journey).
 */
import { StrictMode, Suspense } from "react";
import { render, screen, act } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { PHOTO_PROMPT_EXPLAINER_ID } from "@/components/cv/PhotoPromptStep";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));
vi.mock("@/components/cv/TemplateSelector", () => ({ TemplateSelector: () => <div data-testid="template-picker" /> }));

import CVPage from "../cv/page";

describe("CVPage — the first phase is decided once", () => {
  afterEach(() => vi.restoreAllMocks());

  it("a cleaned-up first run does not put the page back to the photo question", async () => {
    let profileCalls = 0;
    let settingsCalls = 0;
    let releaseFirstProfile: () => void = () => {};
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/state")) {
        return { ok: true, json: async () => ({ job_id: "job-1", current_step: "cv_generation", cv_summary: null }) } as Response;
      }
      if (url.includes("/api/cv?job_id=")) return { ok: true, json: async () => [] } as Response;
      if (url.includes("/api/settings")) {
        settingsCalls += 1;
        return { ok: true, json: async () => ({ dismissed_explainers: [PHOTO_PROMPT_EXPLAINER_ID] }) } as Response;
      }
      if (url.includes("/api/profile")) {
        profileCalls += 1;
        if (profileCalls === 1) await new Promise<void>((r) => (releaseFirstProfile = r));
        return { ok: true, json: async () => ({ profile: { personal_info: { photo_url: null } } }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });

    await act(async () => {
      render(
        withIntl(
          <StrictMode>
            <Suspense fallback={null}>
              <CVPage params={Promise.resolve({ flowId: "f1" })} />
            </Suspense>
          </StrictMode>,
          "de",
        ),
      );
    });
    // The live run reached the photo question; the remembered skip moved on.
    expect(await screen.findByTestId("template-picker")).toBeInTheDocument();
    const settingsBefore = settingsCalls;

    // Now the first (cleaned-up) run's profile answer arrives.
    await act(async () => {
      releaseFirstProfile();
      await Promise.resolve();
    });
    await act(async () => {});
    expect(screen.getByTestId("template-picker")).toBeInTheDocument();
    // It must not have mounted the photo step again.
    expect(settingsCalls).toBe(settingsBefore);
  });
});
