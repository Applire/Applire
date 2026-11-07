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

import { render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { GenerationProgress } from "../GenerationProgress";
import { withIntl } from "@/lib/test-utils/with-intl";

const DEFAULT_PROPS = {
  cvId: "test-cv-id",
  flowId: "test-flow-id",
  onReady: vi.fn(),
  onRetry: vi.fn(),
};

describe("GenerationProgress", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders only real phase steps — no 'Done!' placeholder step (#114)", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    expect(screen.getByText("In queue…")).toBeInTheDocument();
    expect(screen.getByText("Rendering CV…")).toBeInTheDocument();
    // "Done!" is not a phase of the run — it must never render as a pending step.
    expect(screen.queryByText("Done!")).not.toBeInTheDocument();
  });

  it("marks queued step active and others pending on initial render", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "pending",
        error_message: null,
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    const queued = screen.getByText("In queue…").closest("[data-step-status]");
    expect(queued).toHaveAttribute("data-step-status", "active");
    const rendering = screen.getByText("Rendering CV…").closest("[data-step-status]");
    expect(rendering).toHaveAttribute("data-step-status", "pending");
  });

  it("marks queued done and rendering active when status is generating", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "generating",
        error_message: null,
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await waitFor(() => {
      const queued = screen.getByText("In queue…").closest("[data-step-status]");
      expect(queued).toHaveAttribute("data-step-status", "done");
      const rendering = screen.getByText("Rendering CV…").closest("[data-step-status]");
      expect(rendering).toHaveAttribute("data-step-status", "active");
    });
  });

  it("shows a localized truncation message and retry when error_code is llm_truncated", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "failed",
        error_code: "llm_truncated",
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await waitFor(() => {
      expect(
        screen.getByText("Your CV couldn't be generated in one pass — it may be too detailed for the current AI model. Please try again."),
      ).toBeInTheDocument();
      expect(screen.getByText("Try again →")).toBeInTheDocument();
    });
  });

  it("shows a localized timeout message when error_code is llm_timeout", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "failed",
        error_code: "llm_timeout",
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await waitFor(() => {
      expect(
        screen.getByText("Generating your CV took too long. Please try again in a moment."),
      ).toBeInTheDocument();
    });
  });

  it("789:192: shows the provider-outage message when error_code is provider_unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "failed",
        error_code: "provider_unavailable",
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await waitFor(() => {
      expect(
        screen.getByText("The AI provider could not be reached. Please try again in a moment."),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText("Generation failed.")).not.toBeInTheDocument();
  });

  it("falls back to the generic failure message for an unknown error_code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "failed",
        error_code: "generation_failed",
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await waitFor(() => {
      expect(screen.getByText("Generation failed.")).toBeInTheDocument();
      expect(screen.getByText("Try again →")).toBeInTheDocument();
    });
  });

  it("never renders a raw backend error string to the user (PQ F6)", async () => {
    // Even if the backend regresses and leaks raw LLM text, the UI must not show it.
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "failed",
        error_code: "llm_truncated",
        error_message: "Raise max_tokens or reduce reasoning.",
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await waitFor(() => {
      expect(screen.queryByText(/Raise max_tokens/)).not.toBeInTheDocument();
    });
  });

  it("calls onReady when status becomes ready", async () => {
    const onReady = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        cv_id: "test-cv-id",
        status: "ready",
        error_message: null,
        expires_at: "2026-05-01T00:00:00Z",
      }),
    } as Response);

    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} onReady={onReady} />));

    await waitFor(() => {
      expect(onReady).toHaveBeenCalledWith("test-cv-id");
    });
  });
});

// 789:261 (Hazelnut build 7, WP-F): the ring sits at 50 % for the whole write;
// the elapsed time is always shown, and from SLOW_AFTER_MS a "longer than usual"
// line says leaving is safe (the web door records the finished CV, 789:248).
describe("GenerationProgress — long wait (789:261)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("formats the elapsed time as m:ss", async () => {
    const { formatElapsed } = await import("../GenerationProgress");
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(65_400)).toBe("1:05");
    expect(formatElapsed(161_000)).toBe("2:41");
  });

  it("shows the elapsed time, and the slow line only from two minutes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ cv_id: "test-cv-id", status: "generating", error_code: null, expires_at: "2099-01-01T00:00:00Z" }),
    } as Response);
    const { act } = await import("@testing-library/react");
    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} />));

    await act(async () => {
      vi.advanceTimersByTime(61_000);
    });
    expect(screen.getByTestId("generation-elapsed").textContent).toBe("Running for 1:01");
    expect(screen.queryByTestId("generation-slow")).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByTestId("generation-slow").textContent).toContain("You can leave this page");
  });

  it("a caller's subtitle replaces the default one", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: false, status: 500, json: async () => ({}) } as Response);
    render(withIntl(<GenerationProgress {...DEFAULT_PROPS} subtitle="A new version with your interview answers" />));
    expect(screen.getByText("A new version with your interview answers")).toBeInTheDocument();
  });
});
