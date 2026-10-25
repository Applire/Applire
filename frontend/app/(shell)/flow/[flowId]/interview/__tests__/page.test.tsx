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
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InterviewPage from "../page";
import { guardedNavigate } from "@/components/document/useUnsavedDraftGuard";
import { GapClusterCard, TONE } from "@/components/gaps/GapClusterCard";
import { clusterView, type GapCluster } from "@/lib/match-utils";

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
  // Params are appended (JSON) so tests can pin what value a key was
  // interpolated with (issue #245 NEW-4 — "of up to {total}" must receive
  // the real hard_ceiling, not the soft estimate) without needing the real
  // message catalogs loaded.
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${JSON.stringify(params)})` : key,
}));

// F13 — the intro's "seen" mark lives in sessionStorage; every test starts
// in a fresh browser session.
beforeEach(() => {
  window.sessionStorage.clear();
});

/** #789 (mock F1-1 c) — a fresh session opens on the intro card; the question
 * is loaded behind it. Tests that drive the question click through it. */
async function passIntro() {
  await waitFor(() => {
    const settled =
      screen.queryByTestId("interview-intro") ||
      screen.queryByTestId("interview-question") ||
      screen.queryByTestId("interview-start-failed") ||
      screen.queryByTestId("completion-screen") ||
      mockReplace.mock.calls.length > 0;
    if (!settled) throw new Error("interview page not settled yet");
  });
  const start = screen.queryByTestId("interview-intro-start");
  if (start) fireEvent.click(start);
}

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
    await passIntro();

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/flow/f1"));
  });

  it("treats 409 as benign when the flow is already at interview", async () => {
    global.fetch = mockApi({ currentStep: "interview", advanceStatus: 409 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

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
    await passIntro();

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
    await passIntro();

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
    await passIntro();
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
    await passIntro();
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

// ---------------------------------------------------------------------------
// issue #245 — hard-ceiling completion auto-advance + honest question-count
// copy. Founder-acceptance NEW-3/NEW-4: a session that hit hard_ceiling=12
// left the page frozen on the last question (no test previously drove a
// `complete: true` message response through sendAnswer at all — the
// mechanism existed in the code but was entirely unpinned); separately, the
// "~7" soft-estimate copy overshot by 70% because the real ceiling is 12.
// ---------------------------------------------------------------------------

function mockCeilingApi(messageResponse: Record<string, unknown>, hardCeiling = 12) {
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
    if (url.includes("/api/job/j1/gaps")) return { ok: false, status: 404, json: async () => ({}) };
    if (url.includes("/api/session/s1/message")) {
      return { ok: true, json: async () => messageResponse };
    }
    if (url.includes("/api/session")) {
      return {
        ok: true,
        json: async () => ({
          session_id: "s1",
          mode: "targeted",
          first_question: "Tell me about a recent project.",
          question: "Tell me about a recent project.",
          estimated_questions: 7, // the soft midpoint the ceiling overshot
          hard_ceiling: hardCeiling,
          gaps_total: 1,
          gaps_remaining: 1,
          choices: null,
          resumed: false,
        }),
      };
    }
    if (url.includes("/api/flow/f1/advance")) {
      return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
    }
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

describe("InterviewPage hard-ceiling completion (#245)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows the honest 'of up to {hard_ceiling}' progress copy, not the soft ~7 estimate", async () => {
    global.fetch = mockCeilingApi({}, 12) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "Tell me about a recent project.",
      ),
    );

    // total:12 (the real hard_ceiling) — NOT total:7 (the soft midpoint
    // estimated_questions, which is what the founder-acceptance run overshot).
    // #789 — the header names the question number and topic, never "1 of up
    // to {hard_ceiling}" (the ceiling read as daunting, UX audit 2026-10-20).
    expect(document.body.textContent).toContain('questionNumber({"current":1})');
    expect(document.body.textContent).not.toContain("questionOf");
    expect(document.body.textContent).not.toContain('"total":7');
  });

  it("auto-advances to the completion screen when a hard-ceiling turn completes, without a manual reload", async () => {
    const user = userEvent.setup();
    global.fetch = mockCeilingApi({
      complete: true,
      reason: "max_questions_reached",
      questions_asked: 12,
      gaps_resolved: 3,
      gaps_unresolved: ["Kubernetes"],
      completeness_score: 0.8,
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "Tell me about a recent project.",
      ),
    );

    await sendAnswer(user, "We shipped it to production last quarter.");

    // The completion screen must appear on THIS response — no reload, no
    // second fetch, no lingering on the last question.
    await waitFor(() => expect(screen.getByTestId("completion-screen")).toBeInTheDocument());
    expect(screen.queryByTestId("interview-page")).not.toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument(); // questions_asked stat
  });

  it("789:L47 (M3): the completion gauge shows the response's completeness and the profile link goes to /profile", async () => {
    const user = userEvent.setup();
    global.fetch = mockCeilingApi({
      complete: true,
      reason: "user_ended",
      questions_asked: 12,
      gaps_resolved: 4,
      completeness_score: 0.94,
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("Tell me about a recent project."),
    );
    await sendAnswer(user, "Ich bin fertig");

    await waitFor(() => expect(screen.getByTestId("completion-screen")).toBeInTheDocument());
    expect(screen.getByTestId("completeness-gauge")).toHaveAttribute("data-pct", "94");
    // Was href="#" with preventDefault — a dead link.
    expect(screen.getByTestId("view-profile-link")).toHaveAttribute("href", "/profile");
  });

  it("789:L47 (M3): a completion without a score renders no gauge instead of a made-up 0 %", async () => {
    const user = userEvent.setup();
    global.fetch = mockCeilingApi({
      complete: true,
      reason: "user_ended",
      questions_asked: 3,
      gaps_resolved: 1,
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("Tell me about a recent project."),
    );
    await sendAnswer(user, "fertig");

    await waitFor(() => expect(screen.getByTestId("completion-screen")).toBeInTheDocument());
    expect(screen.queryByTestId("completeness-gauge")).not.toBeInTheDocument();
  });

  it("#259 run-4 finding 9: a RESUMED session restores the real question count instead of resetting to 1", async () => {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
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
      if (url.includes("/api/job/j1/gaps")) return { ok: false, status: 404, json: async () => ({}) };
      if (url.includes("/api/session")) {
        return {
          ok: true,
          json: async () => ({
            session_id: "s1",
            mode: "targeted",
            first_question: "Tell me about your Kubernetes experience.",
            question: "Tell me about your Kubernetes experience.",
            estimated_questions: 7,
            hard_ceiling: 12,
            gaps_total: 3,
            gaps_remaining: 2,
            choices: null,
            resumed: true,
            questions_asked: 5,
          }),
        };
      }
      if (url.includes("/api/flow/f1/advance")) {
        return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
      }
      throw new Error(`unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "Tell me about your Kubernetes experience.",
      ),
    );

    // current:5 (the server's real questions_asked) — NOT current:1, the
    // pre-#259 reset every page refresh produced despite real server-side
    // progress.
    expect(document.body.textContent).toContain('questionNumber({"current":5})');
    expect(document.body.textContent).not.toContain('"current":1,');
  });
});

// ---------------------------------------------------------------------------
// issue #256 — a failed turn (provider 503, or any other backend error) must
// never render raw backend text in the chat surface, must re-enable the
// input, and must offer a Retry affordance that re-sends the same answer.
// The pinned vector: apiErrorMessage() used to read body.detail verbatim and
// translateError()'s default branch echoed it straight into the error
// banner — fixed to translate purely from {status, code}, never backend text.
// ---------------------------------------------------------------------------

function mockRetryApi({
  messageResponses,
}: {
  messageResponses: Array<{ ok: true; body: Record<string, unknown> } | { ok: false; status: number; body: unknown }>;
}) {
  const queue = [...messageResponses];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) {
      return {
        ok: true,
        json: async () => ({ job_id: "j1", current_step: "interview", job_summary: { role_title: "Engineer" } }),
      };
    }
    if (url.includes("/api/job/j1/gaps")) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    if (url.includes("/api/session/s1/message")) {
      const next = queue.shift();
      if (!next) throw new Error("mockRetryApi: no more queued message responses");
      if (next.ok) {
        return { ok: true, status: 200, json: async () => next.body };
      }
      return { ok: false, status: next.status, json: async () => next.body };
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
      return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
    }
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

describe("InterviewPage failed-turn recovery (#256)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("never renders the raw provider payload — translates a structured provider_unavailable error, re-enables the input, and Retry re-sends the same answer", async () => {
    const user = userEvent.setup();
    const rawProviderText = "mistralai/mistral-large-latest is temporarily unavailable — do not show me";
    global.fetch = mockRetryApi({
      messageResponses: [
        {
          ok: false,
          status: 503,
          body: { detail: { error_code: "provider_unavailable", message: rawProviderText } },
        },
        { ok: true, body: { complete: false, question: "Tell me about Kubernetes.", gaps_remaining: 1 } },
      ],
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("What is your Docker experience?"),
    );

    await sendAnswer(user, "I have used Docker for 3 years.");

    // The raw provider payload must never appear anywhere in the DOM.
    await waitFor(() => expect(screen.getByTestId("retry-answer-button")).toBeInTheDocument());
    expect(document.body.textContent).not.toContain(rawProviderText);

    // The input must be re-enabled (not permanently disabled) after the failure.
    const textarea = screen.getByTestId("answer-textarea") as HTMLTextAreaElement;
    expect(textarea).not.toBeDisabled();

    // A Retry affordance must be present.
    const retryButton = await screen.findByTestId("retry-answer-button");
    await user.click(retryButton);

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("Tell me about Kubernetes."),
    );
  });

  it("R2-13: an answer the server could not save goes back into the composer, with the not-saved notice and no follow-up", async () => {
    const user = userEvent.setup();
    const answerText = "Since March I am also Team Lead Payments at Acme.";
    global.fetch = mockRetryApi({
      messageResponses: [
        {
          ok: false,
          status: 503,
          body: {
            detail: {
              error_code: "answer_not_saved",
              turn_state: "not_saved",
              message: "Your answer could not be saved. Please send it again.",
            },
          },
        },
        { ok: true, body: { complete: false, question: "Tell me about Kubernetes.", gaps_remaining: 1 } },
      ],
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("What is your Docker experience?"),
    );

    await sendAnswer(user, answerText);

    // The notice is the UI's own copy for the code, never the backend message.
    await waitFor(() => expect(screen.getByTestId("turn-error")).toHaveTextContent("answerNotSaved"));
    expect(document.body.textContent).not.toContain("Please send it again");
    // The text is back in the composer, word for word, and editable.
    const textarea = screen.getByTestId("answer-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe(answerText);
    expect(textarea).not.toBeDisabled();
    // Not-saved is not a generic failure: the composer IS the retry, no second button.
    expect(screen.queryByTestId("retry-answer-button")).not.toBeInTheDocument();
    // The question on screen still stands — no follow-up was asked.
    expect(screen.getByTestId("interview-question")).toHaveTextContent("What is your Docker experience?");

    // Sending again is an ordinary turn.
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("Tell me about Kubernetes."),
    );
    expect(screen.queryByTestId("turn-error")).not.toBeInTheDocument();
  });

  it("never renders a raw Python exception string leaked as a bare-string detail (defense in depth, any status)", async () => {
    const user = userEvent.setup();
    const rawExceptionText = "'NoneType' object is not subscriptable";
    global.fetch = mockRetryApi({
      messageResponses: [
        { ok: false, status: 500, body: { detail: rawExceptionText } },
        { ok: true, body: { complete: false, question: "Tell me about Kubernetes.", gaps_remaining: 1 } },
      ],
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("What is your Docker experience?"),
    );

    await sendAnswer(user, "I have used Docker for 3 years.");

    await waitFor(() => expect(screen.getByTestId("retry-answer-button")).toBeInTheDocument());
    expect(document.body.textContent).not.toContain(rawExceptionText);
    expect(screen.getByTestId("answer-textarea")).not.toBeDisabled();
  });

  it("session-creation failure shows a Retry affordance instead of silently wedging the interview forever", async () => {
    const user = userEvent.setup();
    let sessionCallCount = 0;
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flow/f1/state")) {
        return {
          ok: true,
          json: async () => ({ job_id: "j1", current_step: "interview", job_summary: { role_title: "Engineer" } }),
        };
      }
      if (url.includes("/api/job/j1/gaps")) {
        return { ok: false, status: 404, json: async () => ({}) };
      }
      if (url.includes("/api/session")) {
        sessionCallCount += 1;
        if (sessionCallCount === 1) {
          return {
            ok: false,
            status: 503,
            json: async () => ({
              detail: { error_code: "provider_unavailable", message: "raw provider text — must not render" },
            }),
          };
        }
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
        return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
      }
      throw new Error(`unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    const retryButton = await screen.findByTestId("retry-session-button");
    expect(document.body.textContent).not.toContain("raw provider text — must not render");

    await user.click(retryButton);
    await passIntro();

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent("What is your Docker experience?"),
    );
    expect(sessionCallCount).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// ADR-089 clause 8 — the cluster tracker reflects the SERVER's persisted
// coverage record on a cluster, not just addressed_gap_ids/current tracking:
// a cluster the server already marked covered/declined/spent shows that
// status at load, even though this page's own session never touched it. And
// a turn's own cluster_coverage report (partly_covered) must NOT be painted
// over as "resolved" just because the cluster also appears in
// addressed_gap_ids — the false-green bug clause 8 fixes.
// ---------------------------------------------------------------------------

const COVERAGE_CLUSTERS = [
  {
    id: "c-current",
    label: "Docker",
    category: "C",
    gaps: ["Docker"],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
    coverage: "open",
    budget_remaining: 2,
    member_statuses: [{ member: "Docker", status: "gap" }],
  },
  {
    id: "c-covered",
    label: "Kubernetes",
    category: "C",
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 1, covered: ["Kubernetes"], declined: [], session_ids: ["s0"] },
    coverage: "covered",
    budget_remaining: 1,
    member_statuses: [{ member: "Kubernetes", status: "covered" }],
  },
  {
    id: "c-declined",
    label: "Scala",
    category: "C",
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 1, covered: [], declined: ["Scala"], session_ids: ["s0"] },
    coverage: "declined",
    budget_remaining: 1,
    member_statuses: [{ member: "Scala", status: "declined" }],
  },
  {
    id: "c-spent",
    label: "Helm",
    category: "C",
    gaps: ["Helm"],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 2, covered: [], declined: [], session_ids: ["s0", "s1"] },
    coverage: "partly_covered",
    budget_remaining: 0,
    member_statuses: [{ member: "Helm", status: "gap" }],
  },
  {
    id: "c-left",
    label: "Terraform",
    category: "C",
    gaps: ["Terraform"],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 0, covered: [], declined: [], session_ids: [], left_open: true },
    coverage: "open",
    budget_remaining: 2,
    member_statuses: [{ member: "Terraform", status: "gap" }],
  },
];

describe("InterviewPage tracker reflects the server's own coverage record at load (ADR-089 clause 8)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("renders resolved/declined/spent purely from each cluster's persisted coverage, independent of addressed_gap_ids", async () => {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
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
          json: async () => ({ id: "ga1", match_score: 0.4, gap_clusters: COVERAGE_CLUSTERS }),
        };
      }
      if (url.includes("/api/session")) {
        return {
          ok: true,
          json: async () => ({
            session_id: "s1",
            mode: "targeted",
            first_question: "Tell me about your Docker experience.",
            question: "Tell me about your Docker experience.",
            estimated_questions: 5,
            gaps_total: 4,
            gaps_remaining: 4,
            choices: null,
            resumed: false,
            current_gap_id: "c-current",
            addressed_gap_ids: [],
          }),
        };
      }
      if (url.includes("/api/flow/f1/advance")) {
        return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
      }
      throw new Error(`unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c-current")).toHaveAttribute("data-status", "current"),
    );
    // Not "current" (not the session's current gap) and not addressed by
    // this session's addressed_gap_ids — the server's own coverage record
    // alone is what drives these.
    expect(screen.getByTestId("gap-cluster-c-covered")).toHaveAttribute("data-status", "resolved");
    expect(screen.getByTestId("gap-cluster-c-declined")).toHaveAttribute("data-status", "declined");
    expect(screen.getByTestId("gap-cluster-c-spent")).toHaveAttribute("data-status", "spent");
    // Ruling K-1e — the tracker keeps a gap left open on the gaps page and marks it.
    expect(screen.getByTestId("gap-cluster-c-left")).toHaveAttribute("data-status", "left_open");
  });
});

describe("InterviewPage tracker: a turn's cluster_coverage is not painted over as resolved (ADR-089 false-green fix)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows partly_covered, NOT resolved, when the turn reports partly_covered even though the cluster is in addressed_gap_ids", async () => {
    const user = userEvent.setup();
    global.fetch = mockClusterApi([
      {
        complete: false,
        question: "Tell me more.",
        gaps_remaining: 2,
        current_gap_id: "c2",
        addressed_gap_ids: ["c1"],
        cluster_coverage: {
          cluster_id: "c1",
          coverage: "partly_covered",
          open_concepts: ["Terraform"],
          budget_remaining: 1,
        },
      },
    ]) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "current"),
    );

    await sendAnswer(user, "We use Terraform for some of it.");

    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "partly_covered"),
    );
    expect(screen.getByTestId("gap-cluster-c1")).not.toHaveAttribute("data-status", "resolved");
  });

  it("shows resolved when the turn reports coverage covered", async () => {
    const user = userEvent.setup();
    global.fetch = mockClusterApi([
      {
        complete: false,
        question: "Tell me more.",
        gaps_remaining: 2,
        current_gap_id: "c2",
        addressed_gap_ids: ["c1"],
        cluster_coverage: {
          cluster_id: "c1",
          coverage: "covered",
          open_concepts: [],
          budget_remaining: 1,
        },
      },
    ]) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "current"),
    );

    await sendAnswer(user, "We use GCP.");

    await waitFor(() =>
      expect(screen.getByTestId("gap-cluster-c1")).toHaveAttribute("data-status", "resolved"),
    );
  });
});

// ---------------------------------------------------------------------------
// Ruling C-4 — one gap, one colour: the interview tracker paints the SAME
// worst-requirement tone as the gap card, through the same helper.
// ---------------------------------------------------------------------------

const ms = (pairs: [string, "covered" | "partial" | "gap" | "declined"][]) =>
  pairs.map(([member, status]) => ({ member, status }));

function toneCluster(id: string, category: "B" | "C", over: Partial<GapCluster>): GapCluster {
  return {
    id,
    label: `Cluster ${id}`,
    category,
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 1, covered: [], declined: [], session_ids: ["s0"] },
    coverage: "partly_covered",
    budget_remaining: 1,
    member_statuses: [],
    ...over,
  };
}

const TONE_CLUSTERS: GapCluster[] = [
  toneCluster("t-red", "C", {
    gaps: ["Azure", "GCP"],
    outcome: { asked: 1, covered: ["AWS"], declined: [], session_ids: ["s0"] },
    member_statuses: ms([["Azure", "partial"], ["GCP", "gap"], ["AWS", "covered"]]),
  }),
  toneCluster("t-yellow", "C", {
    gaps: ["Terraform"],
    outcome: { asked: 1, covered: ["Ansible", "Pulumi", "Chef"], declined: [], session_ids: ["s0"] },
    member_statuses: ms([["Terraform", "partial"], ["Ansible", "covered"], ["Pulumi", "covered"], ["Chef", "covered"]]),
  }),
  toneCluster("t-green", "C", {
    outcome: { asked: 1, covered: ["Jenkins"], declined: ["Bamboo"], session_ids: ["s0"] },
    coverage: "covered",
    member_statuses: ms([["Jenkins", "covered"], ["Bamboo", "declined"]]),
  }),
  toneCluster("t-grey", "C", {
    outcome: { asked: 1, covered: [], declined: ["Go"], session_ids: ["s0"] },
    coverage: "declined",
    member_statuses: ms([["Go", "declined"]]),
  }),
  toneCluster("t-spent", "C", {
    gaps: ["Prometheus"],
    outcome: { asked: 2, covered: ["Grafana"], declined: [], session_ids: ["s0", "s1"] },
    budget_remaining: 0,
    member_statuses: ms([["Prometheus", "gap"], ["Grafana", "covered"]]),
  }),
  // Legacy rows (no member facts): the same fallback on both screens.
  { id: "t-legacy-b", label: "Legacy B", category: "B", gaps: ["Docker"], jd_skills: [], jd_context: "" } as unknown as GapCluster,
  { id: "t-legacy-c", label: "Legacy C", category: "C", gaps: ["Rust"], jd_skills: [], jd_context: "" } as unknown as GapCluster,
];

describe("Ruling C-4 — the tracker and the gap card derive the same tone for the same cluster", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("every tracker row carries its card's tone, edge class and glyph colour", async () => {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/flow/f1/state")) {
        return { ok: true, json: async () => ({ job_id: "j1", current_step: "interview", job_summary: { role_title: "Engineer" } }) };
      }
      if (url.includes("/api/job/j1/gaps")) {
        return { ok: true, json: async () => ({ id: "ga1", match_score: 0.4, gap_clusters: TONE_CLUSTERS }) };
      }
      if (url.includes("/api/session")) {
        return {
          ok: true,
          json: async () => ({
            session_id: "s1",
            mode: "targeted",
            first_question: "Q?",
            question: "Q?",
            estimated_questions: 5,
            gaps_total: 1,
            gaps_remaining: 1,
            choices: null,
            resumed: false,
            current_gap_id: "t-red",
            addressed_gap_ids: [],
          }),
        };
      }
      if (url.includes("/api/flow/f1/advance")) {
        return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
      }
      throw new Error(`unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() => expect(screen.getByTestId("gap-cluster-t-red")).toBeInTheDocument());

    const expected: Record<string, string> = {
      "t-red": "red",
      "t-yellow": "yellow",
      "t-green": "green",
      "t-grey": "grey",
      "t-spent": "red",
      "t-legacy-b": "yellow",
      "t-legacy-c": "red",
    };
    for (const cluster of TONE_CLUSTERS) {
      const { container, unmount } = render(<GapClusterCard cluster={cluster} view={clusterView(cluster)} />);
      const card = container.querySelector('[data-testid="gap-cluster-card"]') as HTMLElement;
      const row = screen.getByTestId(`gap-cluster-${cluster.id}`);
      const tone = card.getAttribute("data-tone") as keyof typeof TONE;
      expect(tone).toBe(expected[cluster.id]);
      expect(row).toHaveAttribute("data-tone", tone);
      expect(row.className).toContain(TONE[tone].border);
      expect(card.className).toContain(TONE[tone].border);
      const glyph = row.querySelector("[aria-hidden='true']") as Element;
      expect(glyph.getAttribute("class") ?? "").toContain(TONE[tone].icon);
      unmount();
    }
    // The glyph SHAPE still carries the state.
    expect(screen.getByTestId("gap-cluster-t-green")).toHaveAttribute("data-status", "resolved");
    expect(screen.getByTestId("gap-cluster-t-grey")).toHaveAttribute("data-status", "declined");
    expect(screen.getByTestId("gap-cluster-t-spent")).toHaveAttribute("data-status", "spent");
    expect(screen.getByTestId("gap-cluster-t-yellow")).toHaveAttribute("data-status", "partly_covered");
  });
});

// ---------------------------------------------------------------------------
// Hazelnut build 3, #789 (UX audit 2026-10-20) — first contact with the
// interview: an intro before Q1, ONE open-requirement number (ruling F1-2),
// the match ring labelled as match, and a declined topic that names only
// what was declined.
// ---------------------------------------------------------------------------

const LEDGER = (gapStatus: "gap" | "direct") => [
  { concept: "Produktionsverantwortung", fit_weight: 1, status: gapStatus },
  { concept: "MES", fit_weight: 1, status: "gap" },
  { concept: "Lean", fit_weight: 1, status: "partial" },
  { concept: "SAP", fit_weight: 1, status: "direct" },
  { concept: "IFS/BRC", fit_weight: 1, status: "denied" },
  // keyword-only ATS term: not a requirement, never counted
  { concept: "PP/MM", fit_weight: 0, status: "gap" },
];

const F1_CLUSTERS: GapCluster[] = [
  toneCluster("q1", "C", {
    label: "Qualität und Standards",
    coverage: "declined",
    outcome: { asked: 1, covered: ["ISO 9001"], declined: ["IFS/BRC"], session_ids: ["s0"] },
    member_statuses: ms([["IFS/BRC", "declined"], ["ISO 9001", "covered"]]),
    jd_skills: ["IFS/BRC", "ISO 9001"],
  }),
  toneCluster("p1", "C", {
    label: "Produktion",
    coverage: "open",
    gaps: ["PP/MM", "Produktionsverantwortung", "MES"],
    outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
    // lead G: PP/MM is a posting TERM (fit_weight 0) — `requirement: false`
    member_statuses: [
      { member: "PP/MM", status: "gap", requirement: false },
      ...ms([["Produktionsverantwortung", "gap"], ["MES", "gap"]]),
    ],
  }),
];

function mockF1Api(opts: {
  resumed?: boolean;
  questionsAsked?: number;
  messageResponses?: Record<string, unknown>[];
}) {
  const queue = [...(opts.messageResponses ?? [])];
  let gapsReads = 0;
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) {
      return {
        ok: true,
        json: async () => ({ job_id: "j1", current_step: "interview", job_summary: { role_title: "Leiter Operations" } }),
      };
    }
    if (url.includes("/api/job/j1/gaps")) {
      gapsReads += 1;
      // the first read is the page load; every later read follows a turn
      // that closed "Produktionsverantwortung" in the ledger
      const ledger = LEDGER(gapsReads === 1 ? "gap" : "direct");
      return {
        ok: true,
        json: async () => ({
          id: "ga1",
          match_score: 0.44,
          gap_clusters: F1_CLUSTERS,
          keyword_ledger: ledger,
          category_c: ["Produktionsverantwortung", "MES"],
        }),
      };
    }
    if (url.includes("/api/session/s1/message")) {
      const next = queue.shift();
      if (!next) throw new Error("mockF1Api: no more queued message responses");
      return { ok: true, json: async () => next };
    }
    if (url.includes("/api/session")) {
      return {
        ok: true,
        json: async () => ({
          session_id: "s1",
          mode: "targeted",
          first_question: "Wie verantwortest du die Produktion?",
          question: "Wie verantwortest du die Produktion?",
          estimated_questions: 12,
          hard_ceiling: 18,
          gaps_total: 2,
          gaps_remaining: 2,
          choices: null,
          resumed: opts.resumed ?? false,
          questions_asked: opts.questionsAsked ?? 1,
          current_gap_id: "p1",
          addressed_gap_ids: [],
        }),
      };
    }
    if (url.includes("/api/flow/f1/advance")) {
      return { ok: true, status: 200, json: async () => ({}), text: async () => "" };
    }
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
  return { fn, gapsReads: () => gapsReads };
}

describe("Hazelnut b3 #789 — interview intro, one count, match ring", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("a fresh session opens on the intro (why · how long · stop · honest); the question shows after Start", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);

    const intro = await screen.findByTestId("interview-intro");
    expect(screen.queryByTestId("interview-question")).not.toBeInTheDocument();
    expect(intro.textContent).toContain("introWhyBody");
    // the open count the intro names is the ledger's (2 gap rows, fit > 0)
    expect(intro.textContent).toContain('introWhyBody({"count":2})');
    expect(intro.textContent).toContain('introHowLongBody({"count":12})');
    expect(intro.textContent).toContain("introStopBody");
    expect(intro.textContent).toContain("introHonestBody");

    fireEvent.click(screen.getByTestId("interview-intro-start"));
    expect(screen.getByTestId("interview-question")).toHaveTextContent("Wie verantwortest du die Produktion?");
    expect(screen.queryByTestId("interview-intro")).not.toBeInTheDocument();
  });

  it("a resumed session goes straight to its question (no intro)", async () => {
    global.fetch = mockF1Api({ resumed: true, questionsAsked: 4 }).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    await screen.findByTestId("interview-question");
    expect(screen.queryByTestId("interview-intro")).not.toBeInTheDocument();
  });

  // F13 (delivery run) — a reload after "Erste Frage zeigen" landed on the
  // intro again: nothing was answered yet, so the server does not call the
  // session resumed. The intro is shown once per session per browser session.
  it("F13: a reload after Start lands on the current question, not the intro", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    const first = render(<InterviewPage params={fulfilledParams("f1")} />);
    fireEvent.click(await screen.findByTestId("interview-intro-start"));
    first.unmount();

    // The reload: same session, still unanswered (resumed: false, Q1).
    render(<InterviewPage params={fulfilledParams("f1")} />);
    expect(await screen.findByTestId("interview-question")).toHaveTextContent(
      "Wie verantwortest du die Produktion?",
    );
    expect(screen.queryByTestId("interview-intro")).not.toBeInTheDocument();
  });

  it("F13: an intro left without Start shows again on the next visit", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    const first = render(<InterviewPage params={fulfilledParams("f1")} />);
    await screen.findByTestId("interview-intro");
    first.unmount();
    render(<InterviewPage params={fulfilledParams("f1")} />);
    expect(await screen.findByTestId("interview-intro")).toBeInTheDocument();
  });

  it("the back link returns to the gaps page", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    fireEvent.click(await screen.findByTestId("interview-intro-back"));
    expect(mockPush).toHaveBeenCalledWith("/flow/f1/gaps");
  });

  it("header and sidebar show ONE number — the ledger's open requirements — and it moves after a turn", async () => {
    const user = userEvent.setup();
    const api = mockF1Api({
      messageResponses: [
        { complete: false, question: "Und MES?", gaps_remaining: 2, current_gap_id: "p1", addressed_gap_ids: [], completeness_score: null },
      ],
    });
    global.fetch = api.fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    await waitFor(() =>
      expect(screen.getByTestId("interview-open-count")).toHaveTextContent('openRequirements({"count":2})'),
    );
    // never the session's theme counter, never "1 of up to 18"
    expect(document.body.textContent).not.toContain("gapsRemaining");
    expect(document.body.textContent).not.toContain("questionOf");
    expect(screen.getByTestId("interview-current-topic")).toHaveTextContent('currentTopic({"label":"Produktion"})');
    expect(screen.getByTestId("tracker-open-count")).toHaveTextContent('trackerHeading({"count":2,');

    await sendAnswer(user, "Ich leite zwei Fertigungsbereiche.");

    await waitFor(() =>
      expect(screen.getByTestId("interview-open-count")).toHaveTextContent('openRequirements({"count":1})'),
    );
    expect(screen.getByTestId("tracker-open-count")).toHaveTextContent('trackerHeading({"count":1,');
    expect(api.gapsReads()).toBeGreaterThanOrEqual(2);
  });

  it("the sidebar ring is the MATCH value, labelled as match; a turn's completeness never lands on it", async () => {
    const user = userEvent.setup();
    global.fetch = mockF1Api({
      messageResponses: [
        { complete: false, question: "Und MES?", gaps_remaining: 2, current_gap_id: "p1", addressed_gap_ids: [], completeness_score: 0.94 },
      ],
    }).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    const ring = await screen.findByTestId("match-gauge");
    expect(ring).toHaveAttribute("data-pct", "44");
    expect(ring.textContent).toContain("matchWithJob");
    expect(ring.textContent).not.toContain("profileCompleteness");
    expect(screen.queryByTestId("completeness-gauge")).not.toBeInTheDocument();

    await sendAnswer(user, "Ich leite zwei Fertigungsbereiche.");
    await screen.findByText("Und MES?");
    expect(screen.getByTestId("match-gauge")).toHaveAttribute("data-pct", "44");
  });

  it("a declined topic names only the declined term; a covered member of it shows as covered, not 'nicht vorhanden'", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    const row = await screen.findByTestId("gap-cluster-q1");
    expect(row).toHaveAttribute("data-status", "declined");
    const declined = screen.getByTestId("tracker-declined-terms");
    expect(declined.textContent).toContain('coverageDeclinedTerms({"terms":"IFS/BRC"})');
    expect(declined.textContent).not.toContain("ISO 9001");
    const chips = Array.from(row.querySelectorAll('[data-testid="tracker-member"]'));
    const iso = chips.find((c) => c.textContent === "ISO 9001");
    expect(iso).toHaveAttribute("data-state", "covered");
    expect(chips.find((c) => c.textContent === "IFS/BRC")).toHaveAttribute("data-state", "declined");
  });
});

describe("lead G — a posting term in the tracker", () => {
  it("draws a requirement:false member neutrally, after the requirements, labelled as a posting term — and never counts it", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();

    const row = await screen.findByTestId("gap-cluster-p1");
    expect(row).toHaveAttribute("data-tone", "red");
    const chips = Array.from(row.querySelectorAll('[data-testid="tracker-member"]'));
    expect(chips.map((c) => c.textContent?.replace(/postingTermSr.*/, ""))).toEqual([
      "Produktionsverantwortung",
      "MES",
      "PP/MM",
    ]);
    const term = chips[2];
    expect(term).toHaveAttribute("data-requirement", "false");
    expect(term.className).toContain("bg-surface-container");
    expect(term.className).not.toContain("border-dashed");
    expect(term.textContent).toContain("postingTermSr");
    expect(term).toHaveAttribute("title", "postingTermTitle");
    expect(chips[0]).not.toHaveAttribute("data-requirement");
    // the ledger's fit_weight 0 row is not a requirement: 2 open, not 3
    expect(screen.getByTestId("tracker-open-count")).toHaveTextContent('trackerHeading({"count":2,');
  });
});

// #789 "numbers that agree": the interview's duration is ONE phrase on every
// surface that names it — the first-run step, the gaps CTA hint, the intro.
import deCatalog from "@/messages/de.json";
import enCatalog from "@/messages/en.json";
describe("#789 one interview duration across first contact", () => {
  it.each([["de", deCatalog], ["en", enCatalog]] as const)("%s: first run, gaps hint and intro say the same 10–20", (_l, c) => {
    const cat = c as unknown as Record<string, Record<string, string>>;
    for (const v of [cat.home.step4Tag, cat.gaps.gapInterviewHintCount, cat.interview.introHowLongBody]) {
      expect(v).toContain("10–20");
    }
  });
});

describe("#787 L7 — the question number is the server's gap-question count", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("a confirmation turn the server does not count leaves 'Frage N' where it was", async () => {
    const user = userEvent.setup();
    global.fetch = mockClusterApi([
      // a skill-merge confirmation turn: questions_asked unchanged (1)
      { complete: false, question: "Is SAP PP the same as SAP?", current_gap_id: "c1", addressed_gap_ids: [], questions_asked: 1 },
      { complete: false, question: "Tell me about Docker.", current_gap_id: "c2", addressed_gap_ids: ["c1"], questions_asked: 2 },
    ]) as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    await passIntro();
    await waitFor(() => screen.getByTestId("interview-question-number"));
    expect(screen.getByTestId("interview-question-number").textContent).toContain('"current":1');

    await sendAnswer(user, "Yes, three years.");
    await waitFor(() => expect(screen.getByTestId("interview-question")).toHaveTextContent("SAP PP"));
    expect(screen.getByTestId("interview-question-number").textContent).toContain('"current":1');

    await sendAnswer(user, "Same thing.");
    await waitFor(() => expect(screen.getByTestId("interview-question")).toHaveTextContent("Docker"));
    expect(screen.getByTestId("interview-question-number").textContent).toContain('"current":2');
  });
});

// MD3-1 (adversarial A-5, build-2 F-B) — an unsent answer is a draft: the
// shell nav, the browser back button and a reload ask first, the section
// editor's guard.
describe("Hazelnut b3 MD3-1 — unsent interview answer", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  function unloadPrevented(): boolean {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  }

  async function typeDraft(text: string) {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    fireEvent.click(await screen.findByTestId("interview-intro-start"));
    fireEvent.change(screen.getByTestId("answer-textarea"), { target: { value: text } });
  }

  it("no draft, no guard: reload and the shell nav pass", async () => {
    global.fetch = mockF1Api({}).fn as unknown as typeof fetch;
    render(<InterviewPage params={fulfilledParams("f1")} />);
    fireEvent.click(await screen.findByTestId("interview-intro-start"));
    expect(unloadPrevented()).toBe(false);
    const push = vi.fn();
    guardedNavigate("/dashboard", push);
    expect(push).toHaveBeenCalledWith("/dashboard");
    expect(screen.queryByTestId("edit-unsaved-dialog")).not.toBeInTheDocument();
  });

  it("a typed answer holds a reload (beforeunload)", async () => {
    await typeDraft("Ich habe die Linie 3 geführt");
    await waitFor(() => expect(unloadPrevented()).toBe(true));
  });

  it("the shell nav asks; stay keeps the answer, discard follows the link", async () => {
    await typeDraft("Ich habe die Linie 3 geführt");
    const push = vi.fn();
    act(() => guardedNavigate("/dashboard", push));
    expect(push).not.toHaveBeenCalled();
    const dialog = await screen.findByTestId("edit-unsaved-dialog");
    expect(dialog.textContent).toContain("unsavedAnswerTitle");
    expect(dialog.textContent).toContain("unsavedAnswerBody");
    fireEvent.click(screen.getByTestId("edit-unsaved-stay"));
    expect(screen.getByTestId("answer-textarea")).toHaveValue("Ich habe die Linie 3 geführt");
    expect(mockPush).not.toHaveBeenCalled();

    act(() => guardedNavigate("/dashboard", push));
    fireEvent.click(await screen.findByTestId("edit-unsaved-discard"));
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("the browser back button asks (popstate on the guard entry)", async () => {
    await typeDraft("Ich habe die Linie 3 geführt");
    await waitFor(() => expect(unloadPrevented()).toBe(true));
    act(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByTestId("edit-unsaved-dialog")).toBeInTheDocument();
  });

  it("a whitespace-only answer is not a draft", async () => {
    await typeDraft("   ");
    expect(unloadPrevented()).toBe(false);
  });
});
