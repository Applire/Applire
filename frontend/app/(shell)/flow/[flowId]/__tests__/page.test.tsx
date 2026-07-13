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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { StrictMode } from "react";
import { render, waitFor } from "@testing-library/react";
import FlowIndexPage from "../page";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

// React 19 `use()` reads instrumented promises synchronously — avoids
// Suspense plumbing in jsdom.
function fulfilledParams(flowId: string) {
  const p = Promise.resolve({ flowId });
  return Object.assign(p, { status: "fulfilled", value: { flowId } });
}

describe("FlowIndexPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    mockReplace.mockReset();
  });

  it("kicks off exactly ONE gap analysis under StrictMode double-mount", async () => {
    // The Spaghettieis UAT incident: the dev double-mount fired
    // advanceAndRedirect twice → two simultaneous gap-jobs POSTs → two full
    // LLM analyses and a duplicate gap_analyses row the gaps page then read
    // mid-clustering. The effect must run once per flow, like
    // ProcessingOverlay's `started` guard.
    let gapJobPosts = 0;

    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/api/flow/f1/state")) {
        return {
          ok: true,
          json: async () => ({
            flow_id: "f1",
            job_id: "j1",
            user_type: "returning",
            current_step: "jd_analysis",
            available_actions: { next: "gap_analysis" },
          }),
        } as Response;
      }
      if (url.includes("/api/job/j1/gap-jobs") && method === "POST") {
        gapJobPosts += 1;
        return { ok: true, json: async () => ({ gap_job_id: "g1" }) } as Response;
      }
      if (url.includes("/api/job/j1/gap-jobs/g1")) {
        return {
          ok: true,
          json: async () => ({
            status: "ready",
            error_code: null,
            result: { id: "ga1", match_score: 0.5 },
          }),
        } as Response;
      }
      if (url.includes("/api/flow/f1/advance") && method === "POST") {
        return {
          ok: true,
          json: async () => ({ current_step: "gap_analysis" }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    }) as unknown as typeof fetch;

    render(
      <StrictMode>
        <FlowIndexPage params={fulfilledParams("f1")} />
      </StrictMode>,
    );

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/flow/f1/gaps"));
    expect(gapJobPosts).toBe(1);
  });
});
