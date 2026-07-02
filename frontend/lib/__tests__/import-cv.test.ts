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
import { uploadCvAsync, startCvImport, pollCvImport, CVImportError } from "@/lib/import-cv";

function res(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: () => Promise.resolve(body) } as Response;
}

afterEach(() => vi.restoreAllMocks());

describe("uploadCvAsync", () => {
  it("POSTs to /import-jobs then polls and resolves with the result when ready", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      calls.push(u);
      if (u.includes("/import-jobs/")) {
        return res({ status: "ready", error_code: null, result: { profile_id: "p1", status: "DRAFT" } });
      }
      return res({ import_id: "imp-1", status: "pending" }, true, 202);
    });

    const file = new File(["cv"], "cv.pdf", { type: "application/pdf" });
    const out = await uploadCvAsync(file, { pollMs: 1 });

    expect(out.profile_id).toBe("p1");
    expect(calls.some((u) => u.endsWith("/api/profile/import-jobs"))).toBe(true);
    expect(calls.some((u) => u.includes("/api/profile/import-jobs/imp-1"))).toBe(true);
  });

  it("forwards job_id as a query param", async () => {
    let postUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/import-jobs/")) return res({ status: "ready", result: { status: "DRAFT" } });
      postUrl = u;
      return res({ import_id: "imp-1", status: "pending" }, true, 202);
    });
    await uploadCvAsync(new File(["x"], "cv.pdf"), { jobId: "job-9", pollMs: 1 });
    expect(postUrl).toContain("job_id=job-9");
  });

  it("rejects with CVImportError carrying the error_code on a failed import", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/import-jobs/")) return res({ status: "failed", error_code: "llm_truncated", result: null });
      return res({ import_id: "imp-1", status: "pending" }, true, 202);
    });
    await expect(uploadCvAsync(new File(["x"], "cv.pdf"), { pollMs: 1 })).rejects.toMatchObject({
      name: "CVImportError",
      errorCode: "llm_truncated",
    });
  });

  it("keeps polling while pending/processing, then resolves", async () => {
    const statuses = ["pending", "processing", "ready"];
    let i = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/import-jobs/")) {
        const s = statuses[Math.min(i++, statuses.length - 1)];
        return res({ status: s, error_code: null, result: s === "ready" ? { status: "DRAFT" } : null });
      }
      return res({ import_id: "imp-1", status: "pending" }, true, 202);
    });
    const out = await uploadCvAsync(new File(["x"], "cv.pdf"), { pollMs: 1 });
    expect(out.status).toBe("DRAFT");
    expect(i).toBeGreaterThanOrEqual(3);
  });

  it("throws CVImportError when the start POST fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res({}, false, 500));
    await expect(uploadCvAsync(new File(["x"], "cv.pdf"), { pollMs: 1 })).rejects.toBeInstanceOf(CVImportError);
  });
});

// PQ F1: the onboarding overlay must POST every file's import job up-front (so a
// refresh can't lose queued files), THEN poll — hence the start/poll split.
describe("startCvImport / pollCvImport (split start-vs-poll)", () => {
  it("startCvImport POSTs the file and resolves with the import_id WITHOUT polling", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      calls.push(u);
      return res({ import_id: "imp-9", status: "pending" }, true, 202);
    });

    const id = await startCvImport(new File(["cv"], "cv.pdf", { type: "application/pdf" }));

    expect(id).toBe("imp-9");
    expect(calls).toHaveLength(1);
    expect(calls[0].endsWith("/api/profile/import-jobs")).toBe(true);
  });

  it("startCvImport forwards job_id as a query param", async () => {
    let postUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      postUrl = typeof input === "string" ? input : input.toString();
      return res({ import_id: "imp-9", status: "pending" }, true, 202);
    });
    await startCvImport(new File(["x"], "cv.pdf"), { jobId: "job-3" });
    expect(postUrl).toContain("job_id=job-3");
  });

  it("startCvImport rejects with CVImportError when the POST fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res({}, false, 500));
    await expect(startCvImport(new File(["x"], "cv.pdf"))).rejects.toBeInstanceOf(CVImportError);
  });

  it("pollCvImport polls an already-started import to completion", async () => {
    const statuses = ["processing", "ready"];
    let i = 0;
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = typeof input === "string" ? input : input.toString();
      calls.push(u);
      const s = statuses[Math.min(i++, statuses.length - 1)];
      return res({ status: s, error_code: null, result: s === "ready" ? { profile_id: "p7", status: "DRAFT" } : null });
    });

    const out = await pollCvImport("imp-7", { pollMs: 1 });

    expect(out.profile_id).toBe("p7");
    // Only GET polls — no new POST is issued for an already-started import.
    expect(calls.every((u) => u.includes("/api/profile/import-jobs/imp-7"))).toBe(true);
  });

  it("pollCvImport rejects with the error_code on a failed import", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      res({ status: "failed", error_code: "llm_timeout", result: null }),
    );
    await expect(pollCvImport("imp-7", { pollMs: 1 })).rejects.toMatchObject({
      name: "CVImportError",
      errorCode: "llm_timeout",
    });
  });
});
