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

describe("ProcessingOverlay — blocked JD scrape pauses for inline paste (#151)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  it("pauses with a paste textarea and does NOT auto-continue when JD analyze returns jd_fetch_failed", async () => {
    let flowCreated = false;
    let importPosted = false;
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
      if (url.includes("/api/applications")) {
        flowCreated = true;
        return { ok: true, status: 200, json: async () => ({ flow_session_id: "should-not-happen" }) } as Response;
      }
      if (url.includes("/api/flow") && !url.includes("advance") && !url.includes("state")) {
        flowCreated = true;
        return { ok: true, status: 200, json: async () => ({ flow_id: "should-not-happen" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        importPosted = true;
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    // The recovery block with the paste textarea must appear
    await waitFor(
      () => {
        expect(screen.getByTestId("jd-paste-textarea")).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
    expect(
      screen.getByText(
        "The site blocked us from reading that job posting. Paste the job description below to continue."
      )
    ).toBeInTheDocument();
    // Both explicit actions are offered
    expect(screen.getByTestId("jd-paste-submit")).toBeInTheDocument();
    expect(screen.getByTestId("jd-skip-button")).toBeInTheDocument();
    // No hard error block
    expect(screen.queryByTestId("processing-error")).toBeNull();
    // And the pipeline did NOT silently continue: no flow, no uploads, no redirect.
    expect(flowCreated).toBe(false);
    expect(importPosted).toBe(false);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("shows the url_invalid paste copy when error_code is jd_url_invalid", async () => {
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
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    await waitFor(
      () => {
        expect(
          screen.getByText(
            "That doesn't look like a valid URL. Paste the job description below to continue."
          )
        ).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
    expect(screen.getByTestId("jd-paste-textarea")).toBeInTheDocument();
    expect(screen.queryByTestId("processing-error")).toBeNull();
  });

  it("analyzes pasted text and continues the pipeline with the job (no jd_status)", async () => {
    const analyzeBodies: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/job/analyze")) {
        const body = (init?.body as string) ?? "{}";
        analyzeBodies.push(body);
        if (body.includes('"url"')) {
          return {
            ok: false,
            status: 422,
            statusText: "Unprocessable Entity",
            json: async () => ({ detail: { error_code: "jd_fetch_failed", message: "blocked" } }),
          } as Response;
        }
        return { ok: true, status: 200, json: async () => ({ id: "job-pasted", role_title: "QA" }) } as Response;
      }
      if (url.includes("/api/applications")) {
        return { ok: true, status: 200, json: async () => ({ flow_session_id: "flow-pasted" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: {} }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      if (url.includes("/api/flow/flow-pasted/state")) {
        return { ok: true, status: 200, json: async () => ({ job_id: "job-pasted" }) } as Response;
      }
      if (url.includes("/api/job/job-pasted/gap-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "ready", error_code: null, result: { id: "gap-1", match_score: 0.8 } }) } as Response;
      }
      if (url.includes("/api/job/job-pasted/gap-jobs")) {
        return { ok: true, status: 202, json: async () => ({ gap_job_id: "gj-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    await waitFor(() => expect(screen.getByTestId("jd-paste-textarea")).toBeInTheDocument(), {
      timeout: 5000,
    });

    fireEvent.change(screen.getByTestId("jd-paste-textarea"), {
      target: { value: "Senior QA Engineer at Acme. 5 years of testing experience required." },
    });
    fireEvent.click(screen.getByTestId("jd-paste-submit"));

    // The pipeline resumes exactly as if the URL had worked: flow created from the
    // analyzed job, gaps redirect WITHOUT any jd_status param.
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/flow/flow-pasted/gaps"), {
      timeout: 5000,
    });
    expect(analyzeBodies.length).toBe(2);
    expect(JSON.parse(analyzeBodies[1])).toEqual({
      text: "Senior QA Engineer at Acme. 5 years of testing experience required.",
    });
  });

  it("shows the analyze error inline and keeps the textarea when the paste fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/job/analyze")) {
        const body = (init?.body as string) ?? "{}";
        if (body.includes('"url"')) {
          return {
            ok: false,
            status: 422,
            statusText: "Unprocessable Entity",
            json: async () => ({ detail: { error_code: "jd_fetch_failed", message: "blocked" } }),
          } as Response;
        }
        // Pasted text rejected (US159 not-a-JD path → plain string detail)
        return {
          ok: false,
          status: 422,
          statusText: "Unprocessable Entity",
          json: async () => ({ detail: "That doesn't look like a job description." }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    render(withIntl(<ProcessingOverlay {...DEFAULT_PROPS} />));

    await waitFor(() => expect(screen.getByTestId("jd-paste-textarea")).toBeInTheDocument(), {
      timeout: 5000,
    });

    fireEvent.change(screen.getByTestId("jd-paste-textarea"), {
      target: { value: "some pasted content" },
    });
    fireEvent.click(screen.getByTestId("jd-paste-submit"));

    await waitFor(() => expect(screen.getByTestId("jd-paste-error")).toBeInTheDocument(), {
      timeout: 5000,
    });
    expect(screen.getByTestId("jd-paste-error")).toHaveTextContent(
      "That doesn't look like a job description."
    );
    // The textarea stays so the user can fix and retry
    expect(screen.getByTestId("jd-paste-textarea")).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
    expect(screen.queryByTestId("processing-error")).toBeNull();
  });

  it("explicit continue-without preserves the old JD-less behaviour (flow without job + jd_status param)", async () => {
    let flowBody: string | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/job/analyze")) {
        return {
          ok: false,
          status: 422,
          statusText: "Unprocessable Entity",
          json: async () => ({ detail: { error_code: "jd_fetch_failed", message: "blocked" } }),
        } as Response;
      }
      if (url.includes("/api/flow") && !url.includes("advance") && !url.includes("state")) {
        flowBody = (init?.body as string) ?? null;
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-skip" }) } as Response;
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

    await waitFor(() => expect(screen.getByTestId("jd-skip-button")).toBeInTheDocument(), {
      timeout: 5000,
    });
    fireEvent.click(screen.getByTestId("jd-skip-button"));

    await waitFor(
      () => expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("/flow/flow-skip/gaps")),
      { timeout: 5000 }
    );
    const pushedUrl = mockPush.mock.calls[0][0] as string;
    expect(pushedUrl).toContain("jd_status=fetch_failed");
    // The flow was created WITHOUT a job — old degraded behaviour, now an explicit choice.
    expect(flowBody).not.toBeNull();
    expect(JSON.parse(flowBody as unknown as string)).toEqual({ job_id: null });
    // The amber "skipped" note is shown, like before.
    expect(screen.getByText("The site blocked us — you can paste the text later")).toBeInTheDocument();
  });
});

describe("ProcessingOverlay — JD URL error handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
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

describe("ProcessingOverlay — step list reflects the actual run plan (#114 / blind PQ F10)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  it("plans no JD step and no gaps step when no JD was provided", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ flow_id: "flow-nojd" }),
    } as Response);

    render(
      withIntl(
        <ProcessingOverlay files={[mockFile]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />
      )
    );

    expect(screen.queryByText("Analyzing job description…")).not.toBeInTheDocument();
    expect(screen.queryByText("Detecting Gaps")).not.toBeInTheDocument();
    expect(screen.getByText("Uploading CV")).toBeInTheDocument();
    expect(screen.getByText("Building profile…")).toBeInTheDocument();
  });

  it("plans the JD and gaps steps when a JD text is provided", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ flow_id: "flow-jd" }),
    } as Response);

    render(
      withIntl(
        <ProcessingOverlay files={[mockFile]} jdMode="text" jdUrl="" jdText="Senior QA Engineer at Acme" onCancel={vi.fn()} />
      )
    );

    expect(screen.getByText("Analyzing job description…")).toBeInTheDocument();
    expect(screen.getByText("Detecting Gaps")).toBeInTheDocument();
  });

  it("no-JD run keeps per-file failure/retry aligned with the shorter step list", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-nojd-fail" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return { ok: true, status: 200, json: async () => ({ status: "failed", error_code: "invalid_document", result: null }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "Emma_CV.pdf", { type: "application/pdf" });
    render(withIntl(<ProcessingOverlay files={[f1]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />));

    // The failed file surfaces against ITS step (index 0 in the no-JD plan).
    await waitFor(
      () => expect(screen.getByTestId("processing-file-errors")).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(screen.getByText("Emma_CV.pdf")).toBeInTheDocument();
    expect(screen.getByTestId("retry-file-0")).toBeInTheDocument();
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

describe("ProcessingOverlay — #615 not_applied note", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockClear();
  });

  it("renders one localised note naming the count and labels when the merge is partial", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-partial-import" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "ready",
            error_code: null,
            result: {
              merge_status: "partial",
              not_applied: [
                { section: "skills", label: "Kubernetes", reason: "no_op_carried_entry" },
                { section: "languages", label: "Englisch", reason: "no_op_carried_entry" },
              ],
            },
          }),
        } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "cv1.pdf", { type: "application/pdf" });
    render(withIntl(<ProcessingOverlay files={[f1]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />));

    await waitFor(
      () => expect(screen.getByTestId("processing-not-applied")).toBeInTheDocument(),
      { timeout: 5000 },
    );
    const note = screen.getByTestId("processing-not-applied");
    expect(note).toHaveTextContent("2");
    expect(note).toHaveTextContent("Kubernetes");
    expect(note).toHaveTextContent("Englisch");

    await waitFor(
      () => expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("/flow/flow-partial-import/gaps")),
      { timeout: 5000 },
    );
  });

  it("renders nothing when the merge is fully applied", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/flow") && !url.includes("state") && !url.includes("advance")) {
        return { ok: true, status: 200, json: async () => ({ flow_id: "flow-clean-import" }) } as Response;
      }
      if (url.includes("/api/profile/import-jobs/")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "ready", error_code: null,
            result: { merge_status: "applied", not_applied: [] },
          }),
        } as Response;
      }
      if (url.includes("/api/profile/import-jobs")) {
        return { ok: true, status: 202, json: async () => ({ import_id: "imp-1", status: "pending" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });

    const f1 = new File(["cv1"], "cv1.pdf", { type: "application/pdf" });
    render(withIntl(<ProcessingOverlay files={[f1]} jdMode="text" jdUrl="" jdText="" onCancel={vi.fn()} />));

    await waitFor(
      () => expect(mockPush).toHaveBeenCalledWith(expect.stringContaining("/flow/flow-clean-import/gaps")),
      { timeout: 5000 },
    );
    expect(screen.queryByTestId("processing-not-applied")).toBeNull();
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
