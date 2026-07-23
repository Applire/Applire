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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InterviewPage from "../page";

const mockPush = vi.fn();
const mockReplace = vi.fn();
// A real Next.js router is a stable reference across renders. A mock that
// returns a fresh object every call is unstable and (via the init() effect's
// [flowId, router] dependency array) silently re-triggers session creation on
// every re-render — which desyncs any test that drives an answer/response
// cycle after mount (issue #241 item 1 test coverage surfaced this).
const mockRouter = { push: mockPush, replace: mockReplace };

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
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

function mockApi({ currentStep, advanceStatus }: { currentStep: string; advanceStatus: number }) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) {
      return {
        ok: true,
        json: async () => ({
          job_id: "j1",
          current_step: currentStep,
          job_summary: { role_title: "Engineer" },
        }),
      };
    }
    if (url.includes("/api/job/j1/gaps")) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    if (url.includes("/api/session")) {
      return {
        ok: true,
        json: async () => ({
          session_id: "s1",
          mode: "targeted",
          first_question: "What is your Docker experience?",
          question: "What is your Docker experience?",
          estimated_questions: 5,
          gaps_total: 2,
          gaps_remaining: 2,
          choices: null,
          resumed: false,
        }),
      };
    }
    if (url.includes("/api/flow/f1/advance")) {
      return {
        ok: advanceStatus < 400,
        status: advanceStatus,
        json: async () => ({}),
        text: async () => "conflict",
      };
    }
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

describe("InterviewPage flow-advance guard", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    // jsdom does not implement scrollIntoView (used by the chat auto-scroll)
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("bounces to the flow index when advance 409s and the flow is not at interview", async () => {
    global.fetch = mockApi({ currentStep: "jd_analysis", advanceStatus: 409 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/flow/f1"));
  });

  it("treats 409 as benign when the flow is already at interview", async () => {
    global.fetch = mockApi({ currentStep: "interview", advanceStatus: 409 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "What is your Docker experience?",
      ),
    );
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("renders normally when advance succeeds", async () => {
    global.fetch = mockApi({ currentStep: "gap_analysis", advanceStatus: 200 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "What is your Docker experience?",
      ),
    );
    expect(mockReplace).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// issue #241 item 1 — the split-screen cluster tracker must reflect the
// server's own current_gap_id / addressed_gap_ids, never re-derive progress
// from gaps_remaining via array-index arithmetic (the "2 gaps remaining while
// 2 of 3 clusters ✓" / "Q4 re-asked a ✓ cluster" wobble).
// ---------------------------------------------------------------------------

const GAP_CLUSTERS = [
  { id: "c1", label: "Cloud", category: "C", gaps: [], jd_skills: [], jd_context: "" },
  { id: "c2", label: "Docker", category: "C", gaps: [], jd_skills: [], jd_context: "" },
  { id: "c3", label: "Kubernetes", category: "C", gaps: [], jd_skills: [], jd_context: "" },
];

function mockClusterApi(messageResponses: Record<string, unknown>[]) {
  const queue = [...messageResponses];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) {
      return {
        ok: true,
        json: async () => ({
          job_id: "j1",
          current_step: "interview",
          job_summary: { role_title: "Engineer" },
        }),
      };
    }
    if (url.includes("/api/job/j1/gaps")) {
      return {
        ok: true,
        json: async () => ({ id: "ga1", match_score: 0.5, gap_clusters: GAP_CLUSTERS }),
      };
    }
    if (url.includes("/api/session/s1/message")) {
      const next = queue.shift();
      if (!next) throw new Error("mockClusterApi: no more queued message responses");
      return { ok: true, json: async () => next };
    }
    if (url.includes("/api/session")) {
      return {
        ok: true,
        json: async () => ({
          session_id: "s1",
          mode: "targeted",
          first_question: "Tell me about your Cloud experience.",
          question: "Tell me about your Cloud experience.",
          estimated_questions: 5,
          gaps_total: 3,
          gaps_remaining: 3,
          choices: null,
          resumed: false,
          current_gap_id: "c1",
          addressed_gap_ids: [],
        }),
      };
    }
    if (url.includes("/api/flow/f1/advance")) {
      return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
    }
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

async function sendAnswer(user: ReturnType<typeof userEvent.setup>, text: string) {
  const textarea = screen.getByTestId("answer-textarea");
  await user.type(textarea, text);
  await user.click(screen.getByTestId("send-button"));
}

describe("InterviewPage cluster tracker (#241 item 1)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("seeds the current cluster from the server-supplied current_gap_id, not index 0 by default", async () => {
    global.fetch = mockClusterApi([]) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "current"),
    );
    expect(screen.getByTestId("gap-cluster-c2")).toHaveAttribute("data-status", "pending");
    expect(screen.getByTestId("gap-cluster-c3")).toHaveAttribute("data-status", "pending");
  });

  it("marks a cluster resolved only once the server reports it in addressed_gap_ids, and advances current to the server's current_gap_id", async () => {
    const user = userEvent.setup();
    global.fetch = mockClusterApi([
      {
        complete: false,
        question: "Tell me about Docker.",
        gaps_remaining: 2,
        current_gap_id: "c2",
        addressed_gap_ids: ["c1"],
      },
    ]) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "current"),
    );

    await sendAnswer(user, "We use GCP extensively.");

    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "resolved"),
    );
    expect(screen.getByTestId("gap-cluster-c2")).toHaveAttribute("data-status", "current");
    expect(screen.getByTestId("gap-cluster-c3")).toHaveAttribute("data-status", "pending");
  });

  it("does NOT mark the current cluster resolved on a follow-up turn that re-asks the same gap", async () => {
    // Regression pin for the reported "Q4 re-asked a ✓ cluster" wobble: the
    // second turn stays on c2 (current_gap_id unchanged, addressed_gap_ids
    // unchanged) — a follow-up question, not an advance.
    const user = userEvent.setup();
    global.fetch = mockClusterApi([
      {
        complete: false,
        question: "Tell me about Docker.",
        gaps_remaining: 2,
        current_gap_id: "c2",
        addressed_gap_ids: ["c1"],
      },
      {
        complete: false,
        question: "Can you give a more concrete Docker example?",
        gaps_remaining: 2,
        current_gap_id: "c2",
        addressed_gap_ids: ["c1"],
      },
    ]) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "current"),
    );

    await sendAnswer(user, "We use GCP extensively.");
    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c2")).toHaveAttribute("data-status", "current"),
    );

    await sendAnswer(user, "I've touched Docker a little.");

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "Can you give a more concrete Docker example?",
      ),
    );
    // Still current, NOT resolved — the follow-up did not advance the gap.
    expect(screen.getByTestId("gap-cluster-c2")).toHaveAttribute("data-status", "current");
    expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "resolved");
    expect(screen.getByTestId("gap-cluster-c3")).toHaveAttribute("data-status", "pending");
  });
});
