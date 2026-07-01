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

import { describe, it, expect, vi, afterEach } from "vitest";
import { analyzeGapsAsync, GapAnalysisError } from "@/lib/gap-analysis";

function res(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: () => Promise.resolve(body) } as Response;
}

afterEach(() => vi.restoreAllMocks());

describe("analyzeGapsAsync", () => {
  it("POSTs to /gap-jobs then polls and resolves with the analysis when ready", async () => {
    const calls: { url: string; method?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const u = typeof input === "string" ? input : input.toString();
      calls.push({ url: u, method: (init as RequestInit | undefined)?.method });
      if (u.includes("/gap-jobs/")) {
        return res({ status: "ready", error_code: null, result: { id: "a1", match_score: 0.7 } });
      }
      return res({ gap_job_id: "g1", status: "pending" }, true, 202);
    });

    const out = await analyzeGapsAsync("job-1", { pollMs: 1 });

    expect(out.id).toBe("a1");
    expect(out.match_score).toBe(0.7);
    expect(calls[0].url).toBe("/api/job/job-1/gap-jobs");
    expect(calls[0].method).toBe("POST");
    expect(calls.some((c) => c.url.includes("/api/job/job-1/gap-jobs/g1"))).toBe(true);
  });

  it("keeps polling while pending/processing, then resolves", async () => {
    const statuses = ["pending", "processing", "ready"];
    let i = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/gap-jobs/")) {
        const s = statuses[Math.min(i++, statuses.length - 1)];
        return res({ status: s, error_code: null, result: s === "ready" ? { id: "a1", match_score: 0.5 } : null });
      }
      return res({ gap_job_id: "g1", status: "pending" }, true, 202);
    });
    const out = await analyzeGapsAsync("job-1", { pollMs: 1 });
    expect(out.id).toBe("a1");
    expect(i).toBeGreaterThanOrEqual(3);
  });

  it("rejects with GapAnalysisError carrying the error_code on a failed analysis", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/gap-jobs/")) return res({ status: "failed", error_code: "llm_timeout", result: null });
      return res({ gap_job_id: "g1", status: "pending" }, true, 202);
    });
    await expect(analyzeGapsAsync("job-1", { pollMs: 1 })).rejects.toMatchObject({
      name: "GapAnalysisError",
      errorCode: "llm_timeout",
    });
  });

  it("throws GapAnalysisError when the start POST fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res({}, false, 500));
    await expect(analyzeGapsAsync("job-1", { pollMs: 1 })).rejects.toBeInstanceOf(GapAnalysisError);
  });

  it("times out with gap_timeout past maxWaitMs", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/gap-jobs/")) return res({ status: "processing", error_code: null, result: null });
      return res({ gap_job_id: "g1", status: "pending" }, true, 202);
    });
    await expect(
      analyzeGapsAsync("job-1", { pollMs: 1, maxWaitMs: -1 }),
    ).rejects.toMatchObject({ errorCode: "gap_timeout" });
  });
});
