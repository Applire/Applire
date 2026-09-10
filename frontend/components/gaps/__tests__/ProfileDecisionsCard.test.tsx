// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #686 (JF-M-3.5) — a profile dispute raised from a gap answer reaches the
// user, as a corner-anchored toast STACK (founder ruling V-1, 2026-09-09
// reversed the earlier full-width card). Properties pinned here:
//
// 1. Only the two decision-owed threads (conflict, confirmation) produce a
//    popup; an `accuracy` issue is not a decision the candidate owes, and is
//    excluded from both the popup count and the title's count.
// 2. Zero decision-threaded issues -> the stack does not render at all.
// 3. More than MAX_POPUPS (3) decision issues -> exactly 3 popups plus one
//    "+N more" popup for the overflow.
// 4. A popup's CTA opens ProfileReviewDrawer, and the stack itself is not
//    rendered while the drawer is open.
// 5. Dismissal is PER ISSUE: the x on one popup removes only that popup,
//    records only that issue id, and a remount respects it per flowId.
// 6. An auto-dismiss timer hides the whole stack after
//    `notice_auto_dismiss_seconds` without interaction; 0 means never; an
//    auto-hide writes NOTHING to storage (it is not a dismissal).
// 7. An absent `notice_auto_dismiss_seconds` falls back to 30 seconds, not to
//    the "never hide" value 0.
// 8. `refreshToken` changing re-reads /api/profile/health, and a stack that
//    had auto-hidden becomes visible again once there is something to show.
// 9. A rejecting/500ing health fetch leaves the stack hidden and throws
//    nothing.
//
// Plus a unit-test block for `lib/gap-decisions-dismissed.ts`'s per-issue,
// storage-degrades-safely contract (property 10).

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ProfileDecisionsCard, MAX_POPUPS } from "../ProfileDecisionsCard";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import {
  decisionsDismissedKey,
  dismissedDecisions,
  markDecisionDismissed,
} from "@/lib/gap-decisions-dismissed";

vi.mock("@/lib/api/profileReview", () => ({
  startProfileReview: vi.fn(),
  sendProfileReviewMessage: vi.fn(),
}));

import { startProfileReview } from "@/lib/api/profileReview";

const startMock = vi.mocked(startProfileReview);

function issue(thread: HealthIssue["thread"], id: string): HealthIssue {
  return {
    id,
    thread,
    profile_mismatch_severity: "review",
    summary: `${thread} issue ${id}`,
  };
}

function healthResponse(issues: HealthIssue[]): ProfileHealth {
  return { issues, completeness: { score: 0.9, gaps: [], field_gaps: [] } };
}

function jsonResponse(body: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  } as Response;
}

/**
 * Routes the one shared `global.fetch` mock by URL: the component reads both
 * `/api/profile/health` and `/api/settings`. `healthImpl` overrides the
 * health response entirely (used to reject the health call).
 */
function mockFetch(opts: {
  issues?: HealthIssue[];
  healthOk?: boolean;
  healthImpl?: () => Promise<Response>;
  settings?: Record<string, unknown>;
}) {
  const { issues = [], healthOk = true, healthImpl, settings = {} } = opts;
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/api/profile/health")) {
      if (healthImpl) return healthImpl();
      return Promise.resolve(jsonResponse(healthResponse(issues), healthOk));
    }
    if (url.includes("/api/settings")) {
      return Promise.resolve(jsonResponse(settings, true));
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`));
  });
}

/**
 * Flushes the chained microtasks the health/settings effects await, without
 * depending on real OR faked timers — safe to use whether or not
 * `vi.useFakeTimers()` is active. Several small `act`-wrapped ticks rather
 * than one, so React commits the state updates that land between them.
 */
async function flushEffects(rounds = 10) {
  for (let i = 0; i < rounds; i++) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await Promise.resolve();
    });
  }
}

describe("ProfileDecisionsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Which value is right?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: null,
    });
  });

  // Property 1: only conflict + confirmation are counted; accuracy is not —
  // neither in the popup count nor as a popup of its own.
  it("renders one popup per decision-owed issue and counts only conflict + confirmation in the title", async () => {
    global.fetch = mockFetch({
      issues: [issue("conflict", "c-1"), issue("confirmation", "cf-1"), issue("accuracy", "a-1")],
    });

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(2));
    for (const title of screen.getAllByTestId("profile-decisions-title")) {
      expect(title).toHaveTextContent("2 decisions needed to update your profile");
    }
  });

  // Property 2: zero decision-threaded issues -> no stack at all.
  it("renders nothing when there are no decision-threaded issues", async () => {
    global.fetch = mockFetch({ issues: [issue("accuracy", "a-1")] });

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
  });

  it("renders nothing when the issues array is empty", async () => {
    global.fetch = mockFetch({ issues: [] });

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
  });

  // Property 3: more than MAX_POPUPS -> exactly MAX_POPUPS popups plus one
  // "+N more" popup for the rest.
  it("caps at MAX_POPUPS popups and folds the rest into a single '+N more' popup", async () => {
    expect(MAX_POPUPS).toBe(3);
    global.fetch = mockFetch({
      issues: [
        issue("conflict", "d-1"),
        issue("confirmation", "d-2"),
        issue("conflict", "d-3"),
        issue("confirmation", "d-4"),
        issue("conflict", "d-5"),
      ],
    });

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(3));
    expect(screen.getByTestId("profile-decisions-more")).toHaveTextContent("+2 more decisions");
  });

  // Property 4: the CTA opens ProfileReviewDrawer, and the stack itself
  // disappears while the drawer is open.
  it("opens the ProfileReviewDrawer from a popup's CTA and hides the stack while it is open", async () => {
    global.fetch = mockFetch({ issues: [issue("confirmation", "cf-1")] });

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
    expect(screen.queryByTestId("profile-review-drawer")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("profile-decisions-open"));

    expect(screen.getByTestId("profile-review-drawer")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();

    // Let the drawer's own startProfileReview() mock settle before the test
    // ends, so its state update lands inside this test's act(), not the next.
    await waitFor(() => expect(startMock).toHaveBeenCalled());
  });

  // Property 5: dismissal is per issue — the x on ONE popup removes only
  // that popup, records only that issue id, and a remount respects it
  // scoped to the flowId.
  it("dismisses one popup at a time, persists only that issue id per flow, and a remount honours it per flowId", async () => {
    global.fetch = mockFetch({
      issues: [issue("conflict", "d-1"), issue("confirmation", "d-2"), issue("conflict", "d-3")],
    });

    const { unmount } = render(
      withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"),
    );
    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(3));

    const firstCard = screen.getAllByTestId("profile-decisions-card")[0];
    fireEvent.click(within(firstCard).getByTestId("profile-decisions-dismiss"));

    // Only the clicked popup is gone; the other two remain.
    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(2));
    // Only that ONE issue id was recorded — not all three, not zero.
    expect(window.localStorage.getItem(decisionsDismissedKey("flow-1"))).toBe(
      JSON.stringify(["d-1"]),
    );
    unmount();

    // Remount with the SAME flowId — the dismissed one stays hidden, the
    // other two still render, even though the API reports all three again.
    const remountSame = render(
      withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"),
    );
    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(2));
    remountSame.unmount();

    // Remount with a DIFFERENT flowId — nothing is dismissed for this flow,
    // so all three render.
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-2" />, "en"));
    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(3));
  });

  // Property 6: the auto-dismiss timer hides the whole stack after
  // `notice_auto_dismiss_seconds` without interaction; 0 means never; and an
  // auto-hide is not a dismissal — it writes nothing to storage.
  describe("auto-dismiss timer", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("hides the stack after notice_auto_dismiss_seconds elapse, and writes nothing to storage", async () => {
      vi.useFakeTimers();
      global.fetch = mockFetch({
        issues: [issue("confirmation", "cf-1")],
        settings: { notice_auto_dismiss_seconds: 1 },
      });

      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
      // Load-bearing: auto-hidden is not remembered as dismissed.
      expect(window.localStorage.getItem(decisionsDismissedKey("flow-1"))).toBeNull();
      expect(window.localStorage.length).toBe(0);
    });

    it("never auto-hides when notice_auto_dismiss_seconds is 0, however far time advances", async () => {
      vi.useFakeTimers();
      global.fetch = mockFetch({
        issues: [issue("confirmation", "cf-1")],
        settings: { notice_auto_dismiss_seconds: 0 },
      });

      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000 * 60 * 60 * 24); // a full day
      });

      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();
    });
  });

  // Property 7: an absent notice_auto_dismiss_seconds falls back to 30
  // seconds — NOT to 0's "never hide" — so the stack still auto-hides, just
  // on the fallback schedule.
  describe("auto-dismiss fallback", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("falls back to a 30s auto-dismiss when the settings payload omits the field", async () => {
      vi.useFakeTimers();
      global.fetch = mockFetch({
        issues: [issue("confirmation", "cf-1")],
        settings: {}, // no notice_auto_dismiss_seconds key at all
      });

      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();

      // Not yet at 30s: still showing, proving it did not fall back to 0.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(29_999);
      });
      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();

      // At 30s: hidden, pinning the fallback at exactly 30, not some other value.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
    });
  });

  // Property 8: refreshToken changing re-reads /api/profile/health, and a
  // stack that had auto-hidden becomes visible again.
  describe("refreshToken", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("re-reads /api/profile/health when refreshToken changes, and un-hides an auto-hidden stack", async () => {
      vi.useFakeTimers();
      const fetchMock = mockFetch({
        issues: [issue("confirmation", "cf-1")],
        settings: { notice_auto_dismiss_seconds: 1 },
      });
      global.fetch = fetchMock;

      const healthCalls = () =>
        fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/profile/health"))
          .length;

      const { rerender } = render(
        withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={0} />, "en"),
      );
      await flushEffects();
      expect(healthCalls()).toBe(1);
      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();

      // Let the timer auto-hide it.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();

      // Bump refreshToken — re-reads health, and since it succeeds the
      // auto-hide lapses and the stack is visible again.
      rerender(
        withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={1} />, "en"),
      );
      await flushEffects();

      expect(healthCalls()).toBe(2);
      expect(screen.getByTestId("profile-decisions-stack")).toBeInTheDocument();
    });
  });

  // Property 9: a rejecting/500ing health fetch leaves the stack hidden and
  // throws nothing.
  it("leaves the stack hidden and throws nothing when the health fetch rejects", async () => {
    global.fetch = mockFetch({ healthImpl: () => Promise.reject(new Error("network down")) });

    expect(() =>
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en")),
    ).not.toThrow();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
  });

  it("leaves the stack hidden and throws nothing when the health fetch 500s", async () => {
    global.fetch = mockFetch({ issues: [issue("confirmation", "cf-1")], healthOk: false });

    expect(() =>
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en")),
    ).not.toThrow();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
  });
});

// Property 10: the per-issue dismissal store degrades safely and accumulates
// ids rather than replacing them.
describe("gap-decisions-dismissed", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keys the storage entry per flow id", () => {
    expect(decisionsDismissedKey("flow-1")).toBe("applire.gaps.decisionsDismissed.flow-1");
    expect(decisionsDismissedKey("flow-1")).not.toBe(decisionsDismissedKey("flow-2"));
  });

  it("returns an empty Set when nothing has been stored for this flow", () => {
    expect(dismissedDecisions("flow-1")).toEqual(new Set());
  });

  it("returns an empty Set when the stored value is malformed JSON", () => {
    window.localStorage.setItem(decisionsDismissedKey("flow-1"), "{not valid json");
    expect(dismissedDecisions("flow-1")).toEqual(new Set());
  });

  it("returns an empty Set when the stored value is valid JSON but not an array", () => {
    window.localStorage.setItem(decisionsDismissedKey("flow-1"), JSON.stringify({ a: 1 }));
    expect(dismissedDecisions("flow-1")).toEqual(new Set());
  });

  it("returns an empty Set when storage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(dismissedDecisions("flow-1")).toEqual(new Set());
    spy.mockRestore();
  });

  it("never dismisses when the flow id is missing", () => {
    expect(dismissedDecisions(null)).toEqual(new Set());
    expect(dismissedDecisions(undefined)).toEqual(new Set());
  });

  it("accumulates issue ids across calls rather than replacing them", () => {
    markDecisionDismissed("flow-1", "a");
    markDecisionDismissed("flow-1", "b");
    expect(dismissedDecisions("flow-1")).toEqual(new Set(["a", "b"]));
  });

  it("markDecisionDismissed does not throw when storage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => markDecisionDismissed("flow-1", "a")).not.toThrow();
    spy.mockRestore();
  });

  it("markDecisionDismissed is a no-op when the flow id is empty", () => {
    markDecisionDismissed("", "a");
    expect(window.localStorage.length).toBe(0);
  });

  it("markDecisionDismissed is a no-op when the issue id is empty", () => {
    markDecisionDismissed("flow-1", "");
    expect(window.localStorage.length).toBe(0);
  });
});
