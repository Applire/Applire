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

  it("routes a PDF file to /api/profile/upload", async () => {
    const uploadResponse = { completeness_score: 0.85 };
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })         // history
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(uploadResponse) }) // upload
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) });        // history refresh

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "resume.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
      const uploadCall = calls.find((c) => (c[0] as string).includes("/api/profile/upload"));
      expect(uploadCall).toBeDefined();
    });
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
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ completeness_score: 0.84 }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) });

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "cv.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText(/successWithScore/)).toBeInTheDocument();
    });
  });

  it("shows error strip when upload fails", async () => {
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: () => Promise.resolve({ detail: "Datei konnte nicht verarbeitet werden" }),
      });

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["bad"], "bad.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("Datei konnte nicht verarbeitet werden")).toBeInTheDocument();
    });
  });

  // F3 (#72): a standalone update no longer silently bounces the user. It shows a
  // success strip with an explicit "Review what changed" CTA into the merge review.
  it("shows a review CTA after standalone upload success and does not auto-redirect", async () => {
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ completeness_score: 0.9 }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) });

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
  it("suppresses a leaked UUID validation error behind a friendly message", async () => {
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: () =>
          Promise.resolve({
            detail: [{ msg: "Input should be a valid UUID, invalid character: found `n` at 1" }],
          }),
      });

    render(<ProfileImportView />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["bad"], "linkedin.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText(/Invalid input/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/UUID/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pydantic/i)).not.toBeInTheDocument();
  });

  it("navigates to /flow/:id/gaps when flowId is provided", async () => {
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })                    // history on mount
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ completeness_score: 0.9 }) }) // upload
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })                          // history refresh (after upload)
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ job_id: "job-123" }) })        // flow state
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ id: "gap-456" }) })            // gaps
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({}) });                          // advance

    render(<ProfileImportView flowId="flow-abc" />);

    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "cv.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/flow/flow-abc/gaps");
    });
  });

  it("shows success strip and amber flow-error when proceedToGaps fails after upload", async () => {
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })                    // history on mount
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ completeness_score: 0.88 }) }) // upload succeeds
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) })                    // history refresh
      .mockResolvedValueOnce({                                                                  // flow state fails
        ok: false,
        status: 504,
        statusText: "Gateway Timeout",
        json: () => Promise.resolve({ detail: "Upstream timeout" }),
      });

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

  it("shows empty history state when no uploads", async () => {
    global.fetch = makeFetchMock([]);
    render(<ProfileImportView />);
    await waitFor(() => {
      expect(screen.getByText("historyEmpty")).toBeInTheDocument();
    });
  });

  it("opens the merge-gate dialog on a GATED upload instead of navigating", async () => {
    global.fetch = vi.fn((url: unknown) => {
      const u = url as string;
      if (u.includes("/api/profile/uploads"))
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      if (u.includes("/api/profile/upload"))
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: "GATED",
              gate: "name_divergence",
              account_name: "Max Muster",
              cv_name: "Markus Brandt",
              staged_id: "staged-1",
              completeness_score: 0,
            }),
        });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }) as unknown as typeof fetch;

    render(<ProfileImportView />);
    const input = screen.getByTestId("main-file-input");
    const file = new File(["content"], "markus.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(screen.getByTestId("merge-gate-dialog")).toBeInTheDocument());
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("surfaces the review CTA after merging from the gate dialog (F3)", async () => {
    global.fetch = vi.fn((url: unknown) => {
      const u = url as string;
      if (u.includes("/api/profile/uploads"))
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      if (u.includes("/resolve"))
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ action: "merge", profile_id: "p1", completeness_score: 0.9 }),
        });
      if (u.includes("/api/profile/upload"))
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: "GATED",
              gate: "name_divergence",
              account_name: "Max Muster",
              cv_name: "Markus Brandt",
              staged_id: "staged-1",
              completeness_score: 0,
            }),
        });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }) as unknown as typeof fetch;

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
    global.fetch = vi.fn((url: unknown) => {
      const u = url as string;
      if (u.includes("/api/profile/uploads"))
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      if (u.includes("/resolve"))
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ action: "discard" }) });
      if (u.includes("/api/profile/upload"))
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: "GATED",
              gate: "not_a_cv",
              staged_id: "staged-2",
              completeness_score: 0,
            }),
        });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }) as unknown as typeof fetch;

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
