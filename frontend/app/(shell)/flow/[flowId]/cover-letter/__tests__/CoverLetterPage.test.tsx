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
 * CoverLetterPage — buildClProgressSteps unit tests + polling loop tests (Sprint 31)
 */
import { Suspense } from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { buildClProgressSteps } from "../cover-letter-utils";
import type { ProgressStep } from "@/components/ui/progress-widget";

// ─── mocks required for the page component ──────────────────────────────────

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/components/cover-letter/CoverLetterDocument", () => ({
  CoverLetterDocument: () => <div data-testid="cl-document" />,
}));
// Sidebar tab bodies are stubbed — the page renders them via the shared
// RefinementSidebar shell (E038); we only exercise page-level behaviour here.
vi.mock("@/components/cover-letter/CoverLetterContentTab", () => ({
  CoverLetterContentTab: () => <div data-testid="cl-content-tab" />,
}));
vi.mock("@/components/cover-letter/CoverLetterDesignTab", () => ({
  CoverLetterDesignTab: () => <div data-testid="cl-design-tab" />,
}));
vi.mock("@/components/cover-letter/CoverLetterActionsTab", () => ({
  CoverLetterActionsTab: () => <div data-testid="cl-actions-tab" />,
}));
vi.mock("@/components/cover-letter/GenerateCoverLetterModal", () => ({
  GenerateCoverLetterModal: () => <div data-testid="cl-modal" />,
}));

// ─── buildClProgressSteps unit tests ─────────────────────────────────────────

const fakeT = (key: string) =>
  ({ stepPreparing: "Preparing", stepGenerating: "Generating", stepReady: "Done" }[key] ?? key);

describe("buildClProgressSteps", () => {
  it("returns three steps", () => {
    const steps = buildClProgressSteps("pending", fakeT);
    expect(steps).toHaveLength(3);
  });

  it("step 0 is active and others pending when status is pending", () => {
    const steps = buildClProgressSteps("pending", fakeT);
    expect(steps[0].status).toBe("active");
    expect(steps[1].status).toBe("pending");
    expect(steps[2].status).toBe("pending");
  });

  it("step 0 done, step 1 active when status is generating", () => {
    const steps = buildClProgressSteps("generating", fakeT);
    expect(steps[0].status).toBe("done");
    expect(steps[1].status).toBe("active");
    expect(steps[2].status).toBe("pending");
  });

  it("steps 0 and 1 done, step 2 active when status is ready", () => {
    const steps = buildClProgressSteps("ready", fakeT);
    expect(steps[0].status).toBe("done");
    expect(steps[1].status).toBe("done");
    expect(steps[2].status).toBe("active");
  });

  it("unknown status defaults to step 0 active", () => {
    const steps = buildClProgressSteps("queued", fakeT);
    expect(steps[0].status).toBe("active");
    expect(steps[1].status).toBe("pending");
  });

  it("uses translated labels from t()", () => {
    const steps = buildClProgressSteps("pending", fakeT);
    expect(steps.map((s: ProgressStep) => s.label)).toEqual(["Preparing", "Generating", "Done"]);
  });
});

// ─── CoverLetterPage polling loop tests ──────────────────────────────────────

import CoverLetterPage from "../page";

const FLOW_STATE_RESPONSE = {
  cover_letter_summary: {
    cover_letter_id: "cl-abc",
    status: "generating",
    template: "classic_german",
  },
  job_id: "job-123",
  job_summary: { role_title: "Software Engineer" },
};

function renderPage(flowId = "test-flow") {
  const params = Promise.resolve({ flowId });
  return render(
    withIntl(
      <Suspense fallback={<div data-testid="suspense-loading">loading</div>}>
        <CoverLetterPage params={params} />
      </Suspense>
    )
  );
}

describe("CoverLetterPage — polling loop", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows ProgressWidget while status is generating", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/state")) {
        return { ok: true, json: async () => FLOW_STATE_RESPONSE } as Response;
      }
      return {
        ok: true,
        json: async () => ({ status: "generating", letter_data: null }),
      } as Response;
    });

    await act(async () => { renderPage(); });

    await waitFor(
      () => expect(screen.getByText("Creating cover letter")).toBeInTheDocument(),
      { timeout: 5000 }
    );
  });

  it("transitions to ready phase when polling returns status ready", async () => {
    let statusCallCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/state")) {
        return { ok: true, json: async () => FLOW_STATE_RESPONSE } as Response;
      }
      if (url.includes("/status")) {
        statusCallCount++;
        const status = statusCallCount >= 2 ? "ready" : "generating";
        return {
          ok: true,
          json: async () => ({
            status,
            letter_data: status === "ready" ? { header: { name: "Max" } } : null,
          }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });

    await act(async () => { renderPage(); });

    // Wait for generating phase to appear
    await waitFor(
      () => expect(screen.getByText("Creating cover letter")).toBeInTheDocument(),
      { timeout: 5000 }
    );

    // After poll interval fires, the ready state should show the document panel
    await waitFor(
      () => expect(screen.getByTestId("cl-document")).toBeInTheDocument(),
      { timeout: 8000 }
    );
  });

  it("shows the pre-download notice, then downloads on confirm (ADR-040 amended)", async () => {
    const { fireEvent } = await import("@testing-library/react");
    let pdfFetched = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/state")) {
        return { ok: true, json: async () => FLOW_STATE_RESPONSE } as Response;
      }
      if (url.includes("/pdf")) {
        pdfFetched = true;
        return { ok: true, blob: async () => new Blob(["pdf"]) } as Response;
      }
      if (url.includes("/status")) {
        return {
          ok: true,
          json: async () => ({ status: "ready", letter_data: { header: { name: "Max" } } }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });

    await act(async () => { renderPage(); });
    await waitFor(() => expect(screen.getByTestId("cl-document")).toBeInTheDocument(), { timeout: 8000 });

    // Clicking download opens the notice — it does NOT download yet (nudge first).
    await act(async () => { fireEvent.click(screen.getByTestId("document-download-btn")); });
    await waitFor(() => expect(screen.getByTestId("cl-download-review-overlay")).toBeInTheDocument());
    expect(pdfFetched).toBe(false);

    // Confirming the notice proceeds with the download (nudge, not gate).
    await act(async () => { fireEvent.click(screen.getByTestId("predownload-download")); });
    await waitFor(() => expect(pdfFetched).toBe(true));
  });

  it("renders the match score as a percentage — gap_summary.match_score is a 0–1 fraction", async () => {
    const stateWithGap = {
      ...FLOW_STATE_RESPONSE,
      gap_summary: {
        gap_analysis_id: "gap-1",
        match_score: 0.39, // backend stores a 0–1 fraction (39%)
        critical_gaps_count: 0,
        category_c_count: 0,
      },
      cv_summary: { cv_id: "cv-1", expires_at: "2026-09-29T00:00:00Z" },
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/state")) {
        return { ok: true, json: async () => stateWithGap } as Response;
      }
      if (url.includes("/status")) {
        return {
          ok: true,
          json: async () => ({ status: "ready", letter_data: { header: { name: "Max" } } }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });

    await act(async () => { renderPage(); });
    await waitFor(
      () => expect(screen.getByTestId("cl-document")).toBeInTheDocument(),
      { timeout: 8000 }
    );

    // 0.39 → "39%", NOT Math.round(0.39) = "0%"
    expect(screen.getByText("39%")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("shows not-found state when flow has no cover letter summary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ cover_letter_summary: undefined }),
    } as Response);

    await act(async () => { renderPage("missing-flow"); });

    await waitFor(
      () => expect(screen.getByText("Generating cover letter…")).toBeInTheDocument(),
      { timeout: 5000 }
    );
  });
});
