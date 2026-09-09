// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #686 (JF-M-3.5) — the dismissable "N decisions needed" card at the top of
// the gaps page. Properties pinned here:
//
// 1. Only the two decision-owed threads (conflict, confirmation) are counted
//    in the title — an `accuracy` issue is not a decision the candidate owes.
// 2. Zero decision-threaded issues -> the card does not render at all.
// 3. The CTA opens `ProfileReviewDrawer`.
// 4. The x dismisses per-flow via localStorage; a remount with the same
//    flowId stays hidden, a different flowId does not.
// 5. `refreshToken` changing re-reads `/api/profile/health`.
// 6. A rejecting/500ing health fetch leaves the card hidden and throws nothing.
//
// Plus a small unit-test block for `lib/gap-decisions-dismissed.ts`'s
// storage-throws safety (the benign default is "still show it").

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProfileDecisionsCard } from "../ProfileDecisionsCard";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import {
  decisionsDismissedKey,
  isDecisionsDismissed,
  markDecisionsDismissed,
  clearDecisionsDismissed,
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

  // Property 1: only conflict + confirmation are counted; accuracy is not.
  it("counts only conflict + confirmation threads in the title, not accuracy", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse(
        healthResponse([
          issue("conflict", "c-1"),
          issue("confirmation", "cf-1"),
          issue("accuracy", "a-1"),
        ]),
      ),
    );

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
    expect(screen.getByTestId("profile-decisions-title")).toHaveTextContent(
      "2 decisions needed to update your profile",
    );
  });

  // Property 2: zero decision-threaded issues -> no render.
  it("renders nothing when there are no decision-threaded issues", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse(healthResponse([issue("accuracy", "a-1")])),
    );

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
  });

  it("renders nothing when the issues array is empty", async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse(healthResponse([])));

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
  });

  // Property 3: the CTA opens ProfileReviewDrawer.
  it("opens the ProfileReviewDrawer when the CTA is clicked", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse(healthResponse([issue("confirmation", "cf-1")])),
    );

    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));

    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
    expect(screen.queryByTestId("profile-review-drawer")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("profile-decisions-open"));

    expect(screen.getByTestId("profile-review-drawer")).toBeInTheDocument();
  });

  // Property 4: the x dismisses per-flow, persisted in localStorage, and a
  // remount respects it — scoped to the flowId.
  it("dismisses via the x, persists per-flow in localStorage, and a remount honours it", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse(healthResponse([issue("confirmation", "cf-1")])),
    );

    const { unmount } = render(
      withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"),
    );
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("profile-decisions-dismiss"));

    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(decisionsDismissedKey("flow-1"))).toBe("1");
    unmount();

    // Remount with the SAME flowId — stays hidden even though the API still
    // reports the same open decisions.
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();

    // Remount with a DIFFERENT flowId — not dismissed for this flow, so it renders.
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-2" />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
  });

  // Property 5: refreshToken changing re-reads /api/profile/health.
  it("re-reads /api/profile/health when refreshToken changes", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse(healthResponse([issue("confirmation", "cf-1")])),
    );

    const { rerender } = render(
      withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={0} />, "en"),
    );
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));

    rerender(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={1} />, "en"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });

  // Property 6: a rejecting/500ing health fetch leaves the card hidden and throws nothing.
  it("leaves the card hidden and throws nothing when the health fetch rejects", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network down"));

    expect(() =>
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en")),
    ).not.toThrow();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
  });

  it("leaves the card hidden and throws nothing when the health fetch 500s", async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({}, false));

    expect(() =>
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en")),
    ).not.toThrow();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
  });
});

describe("gap-decisions-dismissed", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keys the storage entry per flow id", () => {
    expect(decisionsDismissedKey("flow-1")).toBe("applire.gaps.decisionsDismissed.flow-1");
    expect(decisionsDismissedKey("flow-1")).not.toBe(decisionsDismissedKey("flow-2"));
  });

  it("round-trips through localStorage", () => {
    expect(isDecisionsDismissed("flow-1")).toBe(false);
    markDecisionsDismissed("flow-1");
    expect(isDecisionsDismissed("flow-1")).toBe(true);
    clearDecisionsDismissed("flow-1");
    expect(isDecisionsDismissed("flow-1")).toBe(false);
  });

  it("never dismisses when the flow id is missing", () => {
    expect(isDecisionsDismissed(null)).toBe(false);
    expect(isDecisionsDismissed(undefined)).toBe(false);
    markDecisionsDismissed(null); // must not throw, must not set anything
    expect(window.localStorage.length).toBe(0);
  });

  // The benign default when storage throws (private mode / storage disabled)
  // is "still show it" — every accessor degrades safely.
  it("isDecisionsDismissed degrades to false when storage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(isDecisionsDismissed("flow-1")).toBe(false);
    spy.mockRestore();
  });

  it("markDecisionsDismissed does not throw when storage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => markDecisionsDismissed("flow-1")).not.toThrow();
    spy.mockRestore();
  });

  it("clearDecisionsDismissed does not throw when storage.removeItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(() => clearDecisionsDismissed("flow-1")).not.toThrow();
    spy.mockRestore();
  });
});
