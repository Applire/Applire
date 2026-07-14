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

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProfileImportView } from "../ProfileImportView";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/profile/upload",
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

function makeFetchMock(response: object, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(response),
    statusText: ok ? "OK" : "Error",
  });
}

/**
 * URL-dispatched fetch mock for the async CV import contract (#114 / US177):
 * POST /api/profile/import-jobs → 202 {import_id}; GET /import-jobs/{id} → poll
 * response. `extra` handles any additional endpoints (flow state, gaps, resolve…).
 */
function importJobsMock(
  pollResponse: object | ((url: string) => object),
  extra?: (url: string) => { ok: boolean; status?: number; body: object } | null
) {
  return vi.fn((url: unknown) => {
    const u = url as string;
    const hit = extra?.(u);
    if (hit)
      return Promise.resolve({
        ok: hit.ok,
        status: hit.status ?? (hit.ok ? 200 : 500),
        statusText: hit.ok ? "OK" : "Error",
        json: () => Promise.resolve(hit.body),
      });
    if (u.includes("/api/profile/uploads"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    if (u.includes("/api/profile/import-jobs/")) {
      const body = typeof pollResponse === "function" ? pollResponse(u) : pollResponse;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }
    if (u.includes("/api/profile/import-jobs"))
      return Promise.resolve({
        ok: true,
        status: 202,
        json: () => Promise.resolve({ import_id: "imp-1", status: "pending" }),
      });
    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
  }) as unknown as typeof fetch;
}

const READY = (result: object) => ({ status: "ready", error_code: null, result });

describe("ProfileImportView", () => {
  beforeEach(() => {
    mockPush.mockReset();
    // Default: history endpoint returns empty array
    global.fetch = makeFetchMock([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the dropzone title and browse button", () => {
    render(<ProfileImportView />);
    expect(screen.getByText("dropTitle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "browse" })).toBeInTheDocument();
  });

  it("renders the LinkedIn secondary card", () => {
    render(<ProfileImportView />);
    expect(screen.getByText("linkedinCardTitle")).toBeInTheDocument();
  });

  it("renders its own AppTopbar when used standalone (no flowId)", () => {
    render(<ProfileImportView />);
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  // US223: the flow layout already renders AppTopbar (mode="flow") for every
  // /flow/[flowId]/* route — a second bar here would stack two bars on one
  // page (the /flow/[flowId]/import route regression this guards against).
  it("suppresses its own AppTopbar when used inside a flow to avoid stacking a second bar", () => {
    render(<ProfileImportView flowId="flow-abc" />);
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });

  it("routes a ZIP file to /api/profile/import", async () => {
    const importResponse = { completeness_score: 0.0 };
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(importResponse) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) });

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "linkedin_export.zip", { type: "application/zip" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
      const importCall = calls.find((c) => (c[0] as string).includes("/api/profile/import"));
      expect(importCall).toBeDefined();
    });
  });

  it("shows success strip with completeness score after PDF upload", async () => {
    global.fetch = importJobsMock(READY({ completeness_score: 0.84 }));

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "cv.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText(/successWithScore/)).toBeInTheDocument();
    });
  });

  it("shows a clean localized error strip when the import job can't be created", async () => {
    global.fetch = importJobsMock(READY({}), (u) =>
      /\/api\/profile\/import-jobs$/.test(u)
        ? { ok: false, status: 422, body: { detail: "Datei konnte nicht verarbeitet werden" } }
        : null
    );

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["bad"], "bad.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("uploadFailed")).toBeInTheDocument();
    });
  });

  // F3 (#72): a standalone update no longer silently bounces the user. It shows a
  // success strip with an explicit "Review what changed" CTA into the merge review.
  it("shows a review CTA after standalone upload success and does not auto-redirect", async () => {
    global.fetch = importJobsMock(READY({ completeness_score: 0.9 }));

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "cv.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId("review-merge-cta")).toBeInTheDocument();
    });
    // No silent redirect on success — the user chooses to review.
    expect(mockPush).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("review-merge-cta"));
    expect(mockPush).toHaveBeenCalledWith("/profile#import-log");
  });

  // F3 (#72): a raw pydantic/UUID validation message must never reach the user.
  // On the async path nothing from a failed POST is rendered at all — only the
  // localized generic copy.
  it("suppresses a leaked UUID validation error behind a friendly message", async () => {
    global.fetch = importJobsMock(READY({}), (u) =>
      /\/api\/profile\/import-jobs$/.test(u)
        ? {
            ok: false,
            status: 422,
            body: {
              detail: [{ msg: "Input should be a valid UUID, invalid character: found `n` at 1" }],
            },
          }
        : null
    );

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["bad"], "linkedin.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("uploadFailed")).toBeInTheDocument();
    });
    expect(screen.queryByText(/UUID/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pydantic/i)).not.toBeInTheDocument();
  });

  it("navigates to /flow/:id/gaps when flowId is provided", async () => {
    global.fetch = importJobsMock(READY({ completeness_score: 0.9 }), (u) => {
      if (u.includes("/api/flow/flow-abc/state")) return { ok: true, body: { job_id: "job-123" } };
      if (u.includes("/api/job/job-123/gaps")) return { ok: true, body: { id: "gap-456" } };
      if (u.includes("/api/flow/flow-abc/advance")) return { ok: true, body: {} };
      return null;
    });

    render(<ProfileImportView flowId="flow-abc" />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "cv.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/flow/flow-abc/gaps");
    });
  });

  it("shows success strip and amber flow-error when proceedToGaps fails after upload", async () => {
    global.fetch = importJobsMock(READY({ completeness_score: 0.88 }), (u) =>
      u.includes("/api/flow/flow-xyz/state")
        ? { ok: false, status: 504, body: { detail: "Upstream timeout" } }
        : null
    );

    render(<ProfileImportView flowId="flow-xyz" />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "cv.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      // Upload succeeded — green strip must be visible
      expect(screen.getByTestId("upload-success-strip")).toBeInTheDocument();
      // Navigation error — amber/flow error message must be visible
      expect(screen.getByText(/flowNotFound/i)).toBeInTheDocument();
    });

    // User was NOT redirected
    expect(mockPush).not.toHaveBeenCalled();
  });

  // N1 (post-PQ fast-follow): the common first-timer path is an in-flow import
  // WITHOUT a JD. The flow has no job_id, so gap analysis is not applicable. The
  // page must NOT call /api/job/null/gaps (which 422s and surfaces a spurious
  // "Invalid input" warning) and must show the "Review what changed" CTA so the
  // hand-off matches the standalone path.
  it("does not fetch gaps and shows the review CTA on the in-flow no-JD path", async () => {
    global.fetch = importJobsMock(READY({ completeness_score: 0.99 }), (u) =>
      u.includes("/api/flow/flow-no-jd/state") ? { ok: true, body: { job_id: null } } : null
    );

    render(<ProfileImportView flowId="flow-no-jd" />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "linkedin.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      // The review CTA appears on the in-flow no-JD merge path too.
      expect(screen.getByTestId("review-merge-cta")).toBeInTheDocument();
    });

    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    // Must NOT have attempted the gaps request for a null job_id.
    const gapsCall = calls.find((c) => /\/api\/job\/.*\/gaps/.test(c[0] as string));
    expect(gapsCall).toBeUndefined();
    // No spurious flow/"Invalid input" warning.
    expect(screen.queryByText(/Invalid input/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/flowError/i)).not.toBeInTheDocument();
    // No silent redirect — the user chooses to review.
    expect(mockPush).not.toHaveBeenCalled();
  });

  // #114 (blind PQ F10) / US177: the update-profile upload no longer shows a static
  // "Uploading…" label through minutes of LLM merging. The CV path runs through the
  // async import-jobs API and a stepped ProgressWidget names the actual phase.
  describe("async import with stepped progress (#114 / US177)", () => {
    function urlMock(pollResponse: object) {
      return vi.fn((url: unknown) => {
        const u = url as string;
        if (u.includes("/api/profile/uploads"))
          return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
        if (u.includes("/api/profile/import-jobs/"))
          return Promise.resolve({ ok: true, json: () => Promise.resolve(pollResponse) });
        if (u.includes("/api/profile/import-jobs"))
          return Promise.resolve({
            ok: true,
            status: 202,
            json: () => Promise.resolve({ import_id: "imp-1", status: "pending" }),
          });
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }) as unknown as typeof fetch;
    }

    it("routes a PDF through the async import-jobs API, never the sync /upload", async () => {
      global.fetch = urlMock({ status: "ready", error_code: null, result: { completeness_score: 0.9 } });

      render(<ProfileImportView />);
      fireEvent.change(screen.getByTestId("main-file-input"), {
        target: { files: [new File(["c"], "resume.pdf", { type: "application/pdf" })] },
      });

      await waitFor(() => expect(screen.getByTestId("upload-success-strip")).toBeInTheDocument());
      const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
      expect(calls.some((u) => u.includes("/api/profile/import-jobs"))).toBe(true);
      expect(calls.some((u) => /\/api\/profile\/upload$/.test(u))).toBe(false);
    });

    it("shows the merge phase as active while the import job is processing", async () => {
      // Job never finishes in this test — we only assert the honest phase labels.
      global.fetch = urlMock({ status: "processing", error_code: null, result: null });

      render(<ProfileImportView />);
      fireEvent.change(screen.getByTestId("main-file-input"), {
        target: { files: [new File(["c"], "resume.pdf", { type: "application/pdf" })] },
      });

      await waitFor(() => {
        // Upload finished (POST returned) — the widget names the real phase now.
        const merging = screen.getByText("stepMerging").closest("[data-step-status]");
        expect(merging).toHaveAttribute("data-step-status", "active");
        const uploading = screen.getByText("stepUploading").closest("[data-step-status]");
        expect(uploading).toHaveAttribute("data-step-status", "done");
      });
      // The old static label is gone.
      expect(screen.queryByText("uploading")).not.toBeInTheDocument();
    });

    it("maps a failed import job to clean localized copy (no raw text)", async () => {
      global.fetch = urlMock({ status: "failed", error_code: "llm_truncated", result: null });

      render(<ProfileImportView />);
      fireEvent.change(screen.getByTestId("main-file-input"), {
        target: { files: [new File(["c"], "resume.pdf", { type: "application/pdf" })] },
      });

      await waitFor(() => expect(screen.getByText("uploadTryAgain")).toBeInTheDocument());
    });

    it("opens the merge-gate dialog when the async import resolves GATED", async () => {
      global.fetch = urlMock({
        status: "ready",
        error_code: null,
        result: {
          status: "GATED",
          gate: "name_divergence",
          account_name: "Max Muster",
          cv_name: "Markus Brandt",
          staged_id: "staged-1",
          completeness_score: 0,
        },
      });

      render(<ProfileImportView />);
      fireEvent.change(screen.getByTestId("main-file-input"), {
        target: { files: [new File(["c"], "markus.pdf", { type: "application/pdf" })] },
      });

      await waitFor(() => expect(screen.getByTestId("merge-gate-dialog")).toBeInTheDocument());
      expect(mockPush).not.toHaveBeenCalled();
    });
  });

  it("shows empty history state when no uploads", async () => {
    global.fetch = makeFetchMock([]);
    render(<ProfileImportView />);
    await waitFor(() => {
      expect(screen.getByText("historyEmpty")).toBeInTheDocument();
    });
  });

  it("surfaces the review CTA after merging from the gate dialog (F3)", async () => {
    global.fetch = importJobsMock(
      READY({
        status: "GATED",
        gate: "name_divergence",
        account_name: "Max Muster",
        cv_name: "Markus Brandt",
        staged_id: "staged-1",
        completeness_score: 0,
      }),
      (u) =>
        u.includes("/resolve")
          ? { ok: true, body: { action: "merge", profile_id: "p1", completeness_score: 0.9 } }
          : null
    );

    render(<ProfileImportView />);
    fireEvent.change(screen.getByTestId("main-file-input"), {
      target: { files: [new File(["c"], "markus.pdf", { type: "application/pdf" })] },
    });
    await waitFor(() => expect(screen.getByTestId("merge-gate-dialog")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("gate-merge-btn"));
    await waitFor(() => expect(screen.getByTestId("review-merge-cta")).toBeInTheDocument());
    expect(mockPush).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("review-merge-cta"));
    expect(mockPush).toHaveBeenCalledWith("/profile#import-log");
  });

  it("closes the gate dialog and stays put after discarding", async () => {
    global.fetch = importJobsMock(
      READY({
        status: "GATED",
        gate: "not_a_cv",
        staged_id: "staged-2",
        completeness_score: 0,
      }),
      (u) => (u.includes("/resolve") ? { ok: true, body: { action: "discard" } } : null)
    );

    render(<ProfileImportView />);
    fireEvent.change(screen.getByTestId("main-file-input"), {
      target: { files: [new File(["c"], "manual.pdf", { type: "application/pdf" })] },
    });
    await waitFor(() => expect(screen.getByTestId("merge-gate-dialog")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("gate-discard-btn"));
    await waitFor(() => expect(screen.queryByTestId("merge-gate-dialog")).not.toBeInTheDocument());
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("badges a parked open-gate upload and re-opens the dialog via Review", async () => {
    const historyItems = [
      {
        id: "staged-7",
        original_filename: "Markus_Brandt_CV.pdf",
        mime_type: "application/pdf",
        byte_size: 102400,
        created_at: "2026-06-17T10:00:00Z",
        completeness_score: null,
        gate_status: "name_divergence",
        staged_name: "Markus Brandt",
      },
    ];
    global.fetch = makeFetchMock(historyItems);
    render(<ProfileImportView />);

    await waitFor(() => expect(screen.getByTestId("gate-review-btn")).toBeInTheDocument());
    expect(screen.getByText("badge")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("gate-review-btn"));
    expect(screen.getByTestId("merge-gate-dialog")).toBeInTheDocument();
  });

  it("renders history items from API response", async () => {
    const historyItems = [
      {
        id: "aaa",
        original_filename: "Lebenslauf_2025.pdf",
        mime_type: "application/pdf",
        byte_size: 102400,
        created_at: "2026-05-06T10:00:00Z",
        completeness_score: 0.92,
      },
    ];
    global.fetch = makeFetchMock(historyItems);
    render(<ProfileImportView />);
    await waitFor(() => {
      expect(screen.getByText("Lebenslauf_2025.pdf")).toBeInTheDocument();
    });
  });
});
