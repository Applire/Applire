// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * 783:195 (Hazelnut build 4) — the end-of-interview summary can be reopened:
 * the interview page, opened after the flow moved on with a COMPLETE interview
 * session, shows that session's summary instead of starting a new session and
 * bouncing to the flow index (the old dead end: the summary existed once).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import InterviewPage from "../page";
import { reopenedSummarySessionId } from "@/components/interview/reopened-summary";

const mockPush = vi.fn();
const mockReplace = vi.fn();
const mockRouter = { push: mockPush, replace: mockReplace };
vi.mock("next/navigation", () => ({ useRouter: () => mockRouter }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
const trail = vi.fn();
vi.mock("@/components/review/DecisionTrailReview", () => ({
  DecisionTrailReview: (props: { sessionId?: string | null; mode: string }) => {
    trail(props);
    return <div data-testid="decision-trail">{`${props.mode}:${props.sessionId}`}</div>;
  },
}));

function fulfilledParams(flowId: string) {
  const p = Promise.resolve({ flowId });
  return Object.assign(p, { status: "fulfilled", value: { flowId } });
}

function api(flow: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) return { ok: true, json: async () => ({ job_id: "j1", ...flow }) };
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

beforeEach(() => {
  mockPush.mockReset();
  mockReplace.mockReset();
  trail.mockReset();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

describe("783:195 — reopening the interview summary", () => {
  it("shows the finished session's summary and starts no new session", async () => {
    const fetchMock = api({
      current_step: "cv_generation",
      interview_summary: { session_id: "s-done", mode: "targeted", status: "complete" },
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() => expect(screen.getByTestId("reopened-summary-screen")).toBeInTheDocument());
    expect(screen.getByTestId("decision-trail")).toHaveTextContent("interview:s-done");
    // No session POST, no flow advance, no bounce.
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toEqual([expect.stringContaining("/api/flow/f1/state")]);
    expect(mockReplace).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("back-to-cv-button"));
    expect(mockPush).toHaveBeenCalledWith("/flow/f1/cv");
  });

  it("the rule: only a complete session on a flow past the interview step", () => {
    const done = { session_id: "s", status: "complete" };
    expect(reopenedSummarySessionId({ current_step: "cv_generation", interview_summary: done })).toBe("s");
    expect(reopenedSummarySessionId({ current_step: "complete", interview_summary: done })).toBe("s");
    expect(reopenedSummarySessionId({ current_step: "interview", interview_summary: done })).toBeNull();
    expect(
      reopenedSummarySessionId({ current_step: "cv_generation", interview_summary: { session_id: "s", status: "active" } }),
    ).toBeNull();
    // Skipped via "Generate CV now": no interview to summarise.
    expect(reopenedSummarySessionId({ current_step: "cv_generation", interview_summary: null })).toBeNull();
  });
});
