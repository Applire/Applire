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

/**
 * ProcessingOverlay — JD URL error handling (Sprint 26) + dynamic CV steps (Sprint 31)
 */
import { StrictMode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { ProcessingOverlay } from "../processing-overlay";
import { withIntl } from "@/lib/test-utils/with-intl";

// Shared push mock so happy-path test can assert on navigation
const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const mockFile = new File(["cv content"], "cv.pdf", { type: "application/pdf" });

const DEFAULT_PROPS = {
  files: [mockFile],
  jdMode: "url" as const,
  jdUrl: "https://blocked.example.com/job",
  jdText: "",
  onCancel: vi.fn(),
};

describe("ProcessingOverlay — JD URL error handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  it("marks JD step as skipped and continues to upload when JD analyze returns jd_fetch_failed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();

      // JD analyze → 422 jd_fetch_failed
      if (url.includes("/api/job/analyze")) {
        return {
          ok: false,
          status: 422,
          statusText: "Unprocessable Entity",
          json: async () => ({ detail: { error_code: "jd_fetch_failed", message: "blocked" } }),
        } as Response;
      }

      // Flow creation (bare flow, no job)
      if (url.includes("/api/flow") && !url.includes("advance") && !url.includes("state")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ flow_id: "test-flow-xyz" }),
        } as Response;
      }

      // CV import (async job: POST /import-jobs then poll /import-jobs/{id})
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }

      // Fallback
      return {
        ok: true,
        status: 200,
        json: async () => ({}),
      } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    // JD step skip message must appear
    await waitFor(
      () => {
        expect(
          screen.getByText("The site blocked us — you can paste the text later")
        ).toBeInTheDocument();
      },
      { timeout: 5000 }
    );

    // No hard error block should be rendered
    expect(screen.queryByTestId("processing-error")).toBeNull();

    // Upload step must become active (pipeline continued)
    await waitFor(
      () => {
        expect(screen.getByText("Uploading CV")).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
  });

  it("marks JD step as skipped with url_invalid copy when error_code is jd_url_invalid", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();

      if (url.includes("/api/job/analyze")) {
        return {
          ok: false,
          status: 422,
          statusText: "Unprocessable Entity",
          json: async () => ({ detail: { error_code: "jd_url_invalid", message: "not a valid url" } }),
        } as Response;
      }

      if (url.includes("/api/flow") && !url.includes("advance") && !url.includes("state")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ flow_id: "test-flow-abc" }),
        } as Response;
      }

      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }

      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    await waitFor(
      () => {
        expect(
          screen.getByText("That doesn't look like a valid URL — you can add it later")
        ).toBeInTheDocument();
      },
      { timeout: 5000 }
    );

    expect(screen.queryByTestId("processing-error")).toBeNull();
  });

  it("still hard-stops on unrecognised 422 (no error_code)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();

      if (url.includes("/api/job/analyze")) {
        return {
          ok: false,
          status: 422,
          statusText: "Unprocessable Entity",
          // Old-style plain string detail — should hard-stop
          json: async () => ({ detail: "Some unknown validation error" }),
        } as Response;
      }

      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    await waitFor(
      () => {
        expect(screen.getByTestId("processing-error")).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
  });

  it("renders one upload step per file when multiple CVs are provided", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ flow_id: "multi-flow-xyz" }),
    } as Response);

    const file1 = new File(["cv1"], "cv1.pdf", { type: "application/pdf" });
    const file2 = new File(["cv2"], "cv2.pdf", { type: "application/pdf" });
    const file3 = new File(["cv3"], "cv3.pdf", { type: "application/pdf" });

    render(
      withIntl(
        <ProcessingOverlay
          files={[file1, file2, file3]}
          jdMode="url"
          jdUrl=""
          jdText=""
          onCancel={vi.fn()}
        />
      )
    );

    expect(screen.getByText("Uploading CV 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("Uploading CV 2 of 3")).toBeInTheDocument();
    expect(screen.getByText("Uploading CV 3 of 3")).toBeInTheDocument();
  });
});

describe("ProcessingOverlay — up-front import queue (blind PQ F1)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  // F1 (BLOCKER): uploads used to run strictly sequentially (POST file 1 → poll to
  // completion → POST file 2 …), so a refresh mid-import silently dropped every file
  // after the current one — the server never knew about them. All import jobs must be
  // POSTed BEFORE any polling begins; from then on the backend owns the whole queue.
  it("POSTs every file's import job before polling any of them", async () => {
    const log: string[] = [];
    let posts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-queue" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        log.push(`poll:${url.split("/").pop()}`);
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        posts++;
        log.push(`post:${posts}`);
        return { ok: true, status: 202, json: async () => ({ import_id: `imp-${posts}`, status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "cv1.pdf", { type: "application/pdf" });
    const f2 = new File(["cv2"], "cv2.pdf", { type: "application/pdf" });
    const f3 = new File(["cv3"], "cv3.pdf", { type: "application/pdf" });
    render(
      withIntl(
        <ProcessingOverlay files={[f1, f2, f3]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />
      )
    );

    await waitFor(
      () => expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("/flow/flow-queue/gaps")),
      { timeout: 5000 }
    );

    // Every POST happened before the first poll — the server owns the full queue
    // before any long wait begins, so a refresh can no longer lose queued files.
    const firstPoll = log.findIndex((e) => e.startsWith("poll:"));
    expect(log.filter((e) => e.startsWith("post:"))).toEqual(["post:1", "post:2", "post:3"]);
    expect(firstPoll).toBeGreaterThanOrEqual(3);
    // Polling follows creation order (matches the backend's per-user processing order).
    expect(log[firstPoll]).toBe("poll:imp-1");
  });

  it("a file whose POST fails is marked failed but does not block queueing the rest", async () => {
    let posts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-poststop" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        posts++;
        if (posts === 1) {
          return { ok: false, status: 500, json: async () => ({}) } as Response;
        }
        return { ok: true, status: 202, json: async () => ({ import_id: `imp-${posts}`, status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "cv1.pdf", { type: "application/pdf" });
    const f2 = new File(["cv2"], "cv2.pdf", { type: "application/pdf" });
    render(
      withIntl(
        <ProcessingOverlay files={[f1, f2]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />
      )
    );

    // The second file still got queued and the pipeline completed with 1 of 2.
    await waitFor(
      () => expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("/flow/flow-poststop/gaps")),
      { timeout: 5000 }
    );
    const pushedUrl = mockPush.mock.calls[0][0] as string;
    expect(pushedUrl).toContain("cv_parsed=1");
    expect(pushedUrl).toContain("cv_total=2");
    expect(posts).toBe(2);
  });
});

describe("ProcessingOverlay — happy path navigation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  it("navigates to /flow/{id}/gaps after full pipeline succeeds with a job", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();

      if (url.includes("/api/job/analyze")) {
        return { ok: true, status: 200, json: async () => ({ id: "job-xyz", role_title: "Engineer" }) } as Response;
      }
      if (url.includes("/api/applications")) {
        return { ok: true, status: 200, json: async () => ({ flow_session_id: "flow-happy" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      if (url.includes("/api/flow/flow-happy/state")) {
        return { ok: true, status: 200, json: async () => ({ job_id: "job-xyz" }) } as Response;
      }
      if (url.includes("/api/job/job-xyz/gap-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: { id: "gap-1", match_score: 0.8 } }) } as Response;
      }
      if (url.includes("/api/job/job-xyz/gap-jobs")) {
        return { ok: true, status: 202, json: async () => ({ gap_job_id: "gj-1", status: "pending" }) } as Response;
      }
      if (url.includes("/api/flow/flow-happy/advance")) {
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(
      withIntl(
        <ProcessingOverlay
          files={[mockFile]}
          jdMode="url"
          jdUrl="https://example.com/job"
          jdText=""
          onCancel={vi.fn()}
        />
      )
    );

    await waitFor(
      () => {
        expect(mockPush).toHaveBeenCalledWith("/flow/flow-happy/gaps");
      },
      { timeout: 5000 }
    );
  });

  it("navigates to /flow/{id}/gaps without job when no JD is provided", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();

      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-nojob" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(
      withIntl(
        <ProcessingOverlay
          files={[mockFile]}
          jdMode="url"
          jdUrl=""
          jdText=""
          onCancel={vi.fn()}
        />
      )
    );

    await waitFor(
      () => {
        expect(mockPush).toHaveBeenCalledWith("/flow/flow-nojob/gaps");
      },
      { timeout: 5000 }
    );
  });
});

describe("ProcessingOverlay — React StrictMode double-mount (blind-PQ regression)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  // Regression: the upload-abort AbortController (added for unmount cleanup) combined
  // with the once-only `started` guard left abortRef pointing at an ALREADY-ABORTED
  // controller after StrictMode's mount→unmount→remount. The CV-import fetch then
  // rejected with AbortError BEFORE sending — no /import-jobs POST — so every CV
  // "failed" and the user saw "We couldn't read any of your CVs", while JD-analyze
  // (which carries no signal) still succeeded. Caught only on the real proxied path.
  it("still POSTs the CV import under StrictMode double-mount (signal not pre-aborted)", async () => {
    const importPosts: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      // Mirror native fetch: an already-aborted signal rejects before sending.
      if ((init as RequestInit | undefined)?.signal?.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      if (url.includes("/api/job/analyze")) {
        return { ok: true, status: 200, json: async () => ({ id: "job-sm", role_title: "X" }) } as Response;
      }
      if (url.includes("/api/applications")) {
        return { ok: true, status: 200, json: async () => ({ flow_session_id: "flow-sm" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        importPosts.push(url);
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      if (url.includes("/api/flow/flow-sm/state")) {
        return { ok: true, status: 200, json: async () => ({ job_id: "job-sm" }) } as Response;
      }
      if (url.includes("/api/job/job-sm/gap-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: { id: "gap-1", match_score: 0.8 } }) } as Response;
      }
      if (url.includes("/api/job/job-sm/gap-jobs")) {
        return { ok: true, status: 202, json: async () => ({ gap_job_id: "gj-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(
      <StrictMode>
        {withIntl(
          <ProcessingOverlay
            files={[mockFile]}
            jdMode="url"
            jdUrl="https://example.com/job"
            jdText=""
            onCancel={vi.fn()}
          />
        )}
      </StrictMode>
    );

    await waitFor(
      () => {
        expect(mockPush).toHaveBeenCalledWith("/flow/flow-sm/gaps");
      },
      { timeout: 5000 }
    );
    // The CV was actually sent (not silently dropped by a pre-aborted signal).
    expect(importPosts.length).toBeGreaterThan(0);
    // And no false "couldn't read your CVs" hard error.
    expect(screen.queryByTestId("processing-error")).toBeNull();
  });
});

describe("ProcessingOverlay — per-file CV parse status (US153 / FMEA 2.2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  it("continues with the parsed CVs and routes with cv_parsed/cv_total when one of two fails", async () => {
    let uploadCall = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-partial" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        const failed = url.includes("imp-2");
        return {
          ok: true,
          status: 200,
          json: async () =>
            failed
              ? { status: "failed", error_code: "invalid_document", result: null }
              : { status: "ready", error_code: null, result: {} },
        } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        uploadCall++;
        return { ok: true, status: 202, json: async () => ({ import_id: `imp-${uploadCall}`, status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "cv1.pdf", { type: "application/pdf" });
    const f2 = new File(["cv2"], "cv2.pdf", { type: "application/pdf" });
    render(
      withIntl(<ProcessingOverlay files={[f1, f2]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />)
    );

    await waitFor(
      () => {
        expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("/flow/flow-partial/gaps"));
      },
      { timeout: 5000 }
    );
    const pushedUrl = mockPush.mock.calls[0][0] as string;
    expect(pushedUrl).toContain("cv_parsed=1");
    expect(pushedUrl).toContain("cv_total=2");
    // Partial failure must NOT surface the hard-error block
    expect(screen.queryByTestId("processing-error")).toBeNull();
  });

  it("guided onboarding skips uploads, creates a guided session, advances to interview, and routes there (US156)", async () => {
    let uploadCalled = false;
    let guidedSession: unknown = null;
    let advancedToInterview = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/job/analyze")) {
        return { ok: true, status: 200, json: async () => ({ id: "job-g", role_title: "X" }) } as Response;
      }
      if (url.includes("/api/applications")) {
        return { ok: true, status: 200, json: async () => ({ flow_session_id: "flow-guided" }) } as Response;
      }
      if (url.includes("/api/session")) {
        guidedSession = JSON.parse((init?.body as string) ?? "{}");
        return { ok: true, status: 201, json: async () => ({ session_id: "sess-g", question: "Q1" }) } as Response;
      }
      if (url.includes("/api/flow/flow-guided/advance")) {
        const adv = JSON.parse((init?.body as string) ?? "{}");
        if (adv.step === "interview") advancedToInterview = true;
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        uploadCalled = true;
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        uploadCalled = true;
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(
      withIntl(
        <ProcessingOverlay
          files={[]}
          jdMode="text"
          jdUrl=""
          jdText="Creative Director at Südlicht — lead the brand team."
          guided
          onCancel={vi.fn()}
        />
      )
    );

    await waitFor(
      () => {
        expect(mockPush).toHaveBeenCalledWith("/flow/flow-guided/interview");
      },
      { timeout: 5000 }
    );
    expect(uploadCalled).toBe(false);
    expect(guidedSession).toEqual({ job_id: "job-g", mode: "guided" });
    expect(advancedToInterview).toBe(true);
  });

  // F1/F8 (run3): a failed parse must surface the file + a clean message + Retry,
  // and a clean backend detail (502 truncation) must never read as a hang.
  it("surfaces the failed file with a clean message and a Retry button when all fail", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-502" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "failed", error_code: "llm_truncated", result: null }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "Markus_CV.pdf", { type: "application/pdf" });
    render(withIntl(<ProcessingOverlay files={[f1]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />));

    await waitFor(
      () => expect(screen.getByTestId("processing-file-errors")).toBeInTheDocument(),
      { timeout: 5000 },
    );
    // The file name and the clean backend message are shown — never raw noise.
    expect(screen.getByText("Markus_CV.pdf")).toBeInTheDocument();
    // truncation → the reassuring "nothing was changed" copy (error_code mapped, no raw text)
    expect(screen.getByText(/nothing was changed/i)).toBeInTheDocument();
    // A per-file Retry is offered.
    expect(screen.getByTestId("retry-file-0")).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("retries a single failed file in place and recovers without restarting", async () => {
    let uploadCall = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-retry" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        const failFirst = url.includes("imp-1");
        return {
          ok: true,
          status: 200,
          json: async () =>
            failFirst
              ? { status: "failed", error_code: "llm_truncated", result: null }
              : { status: "ready", error_code: null, result: {} },
        } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        uploadCall++;
        return { ok: true, status: 202, json: async () => ({ import_id: `imp-${uploadCall}`, status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "Markus_CV.pdf", { type: "application/pdf" });
    render(withIntl(<ProcessingOverlay files={[f1]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />));

    await waitFor(() => expect(screen.getByTestId("retry-file-0")).toBeInTheDocument(), {
      timeout: 5000,
    });

    fireEvent.click(screen.getByTestId("retry-file-0"));

    // After a successful retry the per-file error clears.
    await waitFor(
      () => expect(screen.queryByTestId("retry-file-0")).not.toBeInTheDocument(),
      { timeout: 5000 },
    );
  });

  it("hard-stops (no navigation) when ALL CV uploads fail", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-allfail" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "failed", error_code: "invalid_document", result: null }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} jdMode="text" jdUrl="" jdText="" />));

    await waitFor(
      () => {
        expect(screen.getByTestId("processing-error")).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
    expect(mockPush).not.toHaveBeenCalled();
  });
});
