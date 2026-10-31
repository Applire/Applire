// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #686 (JF-M-3.5) — a profile dispute raised from a gap answer reaches the
// user. M4 (UX audit 2026-10-20, ruling UX-2, amends V-1): ONE notice instead
// of one identical popup per decision, a persistent entry point, and on a
// phone no overlay at all. Properties pinned here:
//
// 1. Only the two decision-owed threads (conflict, confirmation) count; an
//    `accuracy` issue is not a decision the candidate owes.
// 2. Zero decision-threaded issues -> nothing renders at all.
// 3. However many decisions are open, there is exactly ONE notice (the title
//    carries the count) — never one per decision, never "+N more".
// 4. The CTA links to the profile health hub (ADR-099 cl. 10: the decisions
//    are answered there only) — no second review drawer on the gaps page.
// 5. The x collapses the notice to its compact entry point and records the
//    decisions it was about, per flowId; a remount keeps it collapsed, a NEW
//    decision brings the full notice back.
// 6. The auto-dismiss timer collapses the notice after
//    `notice_auto_dismiss_seconds` — the entry point stays; 0 means never; an
//    auto-hide writes NOTHING to storage (it is not a dismissal).
// 7. An absent `notice_auto_dismiss_seconds` falls back to 30 seconds, not to
//    the "never hide" value 0.
// 8. `refreshToken` changing re-reads /api/profile/health; a NEW decision
//    un-collapses an auto-hidden notice, re-reading the same ones does not.
// 9. A rejecting/500ing health fetch renders nothing and throws nothing.
// 10. Phone: the compact row is portaled into the page's `mobileSlot` (the top
//     of the fixed decision bar) and stays after the auto-hide.
// 11. Desktop (F-2): once collapsed, the chip is portaled into the top bar's
//     slot instead of floating below the header.
//
// Plus a unit-test block for `lib/gap-decisions-dismissed.ts`'s per-issue,
// storage-degrades-safely contract.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ProfileDecisionsCard, decisionsOrigin } from "../ProfileDecisionsCard";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import { setStorageUserId } from "@/lib/auth/storage";
import {
  decisionsDismissedKey,
  dismissedDecisions,
  markDecisionDismissed,
} from "@/lib/gap-decisions-dismissed";


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
  });

  // Property 1
  it("counts only conflict + confirmation, in ONE notice", async () => {
    global.fetch = mockFetch({
      issues: [issue("conflict", "c-1"), issue("confirmation", "cf-1"), issue("accuracy", "a-1")],
    });
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(1));
    expect(screen.getByTestId("profile-decisions-title")).toHaveTextContent("2 decisions needed to update your profile");
  });

  // Property 2
  it("renders nothing when there are no decision-threaded issues", async () => {
    global.fetch = mockFetch({ issues: [issue("accuracy", "a-1")] });
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    await flushEffects();
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
    expect(screen.queryByTestId("profile-decisions-bar-row")).not.toBeInTheDocument();
  });

  it("renders nothing when the issues array is empty", async () => {
    global.fetch = mockFetch({ issues: [] });
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    await flushEffects();
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
    expect(screen.queryByTestId("profile-decisions-bar-row")).not.toBeInTheDocument();
  });

  // Property 3 — the M4 shape: Emma's Pixel 7 showed three identical notices.
  it("shows exactly one notice for five open decisions — no per-decision copies, no '+N more'", async () => {
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
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
    expect(screen.getAllByTestId("profile-decisions-card")).toHaveLength(1);
    expect(screen.getAllByTestId("profile-decisions-title")).toHaveLength(1);
    expect(screen.getByTestId("profile-decisions-title")).toHaveTextContent("5 decisions needed");
    expect(screen.queryByTestId("profile-decisions-more")).not.toBeInTheDocument();
  });

  // Property 4
  it("links the CTA to the profile health hub and opens no drawer of its own (ADR-099 cl. 10)", async () => {
    global.fetch = mockFetch({ issues: [issue("confirmation", "cf-1")] });
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
    const cta = screen.getByTestId("profile-decisions-open");
    expect(cta.tagName).toBe("A");
    expect(cta).toHaveAttribute("href", "/profile#profile-health");
    expect(cta).toHaveTextContent("View in profile health");
    expect(screen.queryByTestId("profile-review-drawer")).not.toBeInTheDocument();
  });

  it("does not count a class-S issue as a decision", async () => {
    global.fetch = mockFetch({
      issues: [{ ...issue("conflict", "c-1"), delivery_class: "S" }, issue("conflict", "c-2")],
    });
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() =>
      expect(screen.getByTestId("profile-decisions-title")).toHaveTextContent("1 decision"),
    );
  });

  // Property 5
  it("the x collapses to the entry point, records the decisions per flow, and only a NEW decision re-expands", async () => {
    global.fetch = mockFetch({
      issues: [issue("conflict", "d-1"), issue("confirmation", "d-2"), issue("conflict", "d-3")],
    });
    const { unmount } = render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("profile-decisions-dismiss"));

    await waitFor(() => expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument());
    // The entry point stays, with the count.
    expect(screen.getByTestId("profile-decisions-compact")).toHaveTextContent("3 decisions open");
    expect(JSON.parse(window.localStorage.getItem(decisionsDismissedKey("flow-1")) ?? "[]").sort()).toEqual([
      "d-1",
      "d-2",
      "d-3",
    ]);
    unmount();

    // Same flow, same decisions: collapsed from the start.
    const same = render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-decisions-compact")).toBeInTheDocument());
    expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
    same.unmount();

    // Same flow, one NEW decision: the full notice is back.
    global.fetch = mockFetch({
      issues: [issue("conflict", "d-1"), issue("confirmation", "d-2"), issue("conflict", "d-3"), issue("conflict", "d-4")],
    });
    const fresh = render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
    fresh.unmount();

    // Another flow: nothing dismissed there.
    render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-2" />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument());
  });

  // Property 6
  describe("auto-dismiss timer", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("collapses the notice after notice_auto_dismiss_seconds, keeps the entry point, writes nothing", async () => {
      vi.useFakeTimers();
      global.fetch = mockFetch({
        issues: [issue("confirmation", "cf-1")],
        settings: { notice_auto_dismiss_seconds: 1 },
      });
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
      // M4: the auto-hide used to take the only "Decide now" with it.
      expect(screen.getByTestId("profile-decisions-compact")).toBeInTheDocument();
      expect(screen.getByTestId("profile-decisions-compact-open")).toBeInTheDocument();
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
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000 * 60 * 60 * 24);
      });
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
    });
  });

  // Property 7
  describe("auto-dismiss fallback", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("falls back to a 30s auto-dismiss when the settings payload omits the field", async () => {
      vi.useFakeTimers();
      global.fetch = mockFetch({ issues: [issue("confirmation", "cf-1")], settings: {} });
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(29_999);
      });
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
    });
  });

  // Property 8
  describe("refreshToken", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("re-reads health on a new token; only a NEW decision un-collapses an auto-hidden notice", async () => {
      vi.useFakeTimers();
      let issues = [issue("confirmation", "cf-1")];
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/profile/health")) return Promise.resolve(jsonResponse(healthResponse(issues)));
        if (url.includes("/api/settings")) return Promise.resolve(jsonResponse({ notice_auto_dismiss_seconds: 1 }));
        return Promise.reject(new Error(`unexpected fetch: ${url}`));
      });
      global.fetch = fetchMock;
      const healthCalls = () =>
        fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/profile/health")).length;

      const { rerender } = render(
        withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={0} />, "en"),
      );
      await flushEffects();
      expect(healthCalls()).toBe(1);
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();

      // The same decisions again: stays collapsed.
      rerender(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={1} />, "en"));
      await flushEffects();
      expect(healthCalls()).toBe(2);
      expect(screen.queryByTestId("profile-decisions-card")).not.toBeInTheDocument();
      expect(screen.getByTestId("profile-decisions-compact")).toBeInTheDocument();

      // A new one: the full notice is back.
      issues = [issue("confirmation", "cf-1"), issue("conflict", "c-2")];
      rerender(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" refreshToken={2} />, "en"));
      await flushEffects();
      expect(healthCalls()).toBe(3);
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
    });
  });

  // Property 9
  it("renders nothing and throws nothing when the health fetch rejects", async () => {
    global.fetch = mockFetch({ healthImpl: () => Promise.reject(new Error("network down")) });
    expect(() => render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"))).not.toThrow();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
  });

  it("renders nothing and throws nothing when the health fetch 500s", async () => {
    global.fetch = mockFetch({ issues: [issue("confirmation", "cf-1")], healthOk: false });
    expect(() => render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"))).not.toThrow();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
  });

  // Property 11 (F-2)
  describe("desktop: the collapsed chip lives in the top bar", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("after the window the chip is portaled into the top bar's slot, not floating", async () => {
      vi.useFakeTimers();
      const slot = document.createElement("div");
      slot.id = "topbar-notice-slot";
      document.body.appendChild(slot);
      global.fetch = mockFetch({
        issues: [issue("conflict", "d-1")],
        settings: { notice_auto_dismiss_seconds: 1 },
      });
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      // The full notice floats in its corner while it is open.
      expect(screen.getByTestId("profile-decisions-card")).toBeInTheDocument();
      expect(slot.childElementCount).toBe(0);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(within(slot).getByTestId("profile-decisions-compact")).toHaveTextContent("1 decision open");
      expect(screen.queryByTestId("profile-decisions-stack")).not.toBeInTheDocument();
      slot.remove();
    });
  });

  // Property 10
  describe("phone: the entry point docks in the decision bar", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("portals the compact row into mobileSlot, outside the floating layer, and keeps it after the auto-hide", async () => {
      vi.useFakeTimers();
      const slot = document.createElement("div");
      slot.setAttribute("data-testid", "bar-slot");
      document.body.appendChild(slot);
      global.fetch = mockFetch({
        issues: [issue("conflict", "d-1"), issue("conflict", "d-2")],
        settings: { notice_auto_dismiss_seconds: 1 },
      });
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" mobileSlot={slot} />, "en"));
      await flushEffects();

      const row = within(slot).getByTestId("profile-decisions-bar-row");
      expect(row).toHaveTextContent("2 decisions open");
      // Not inside the fixed (desktop) layer.
      expect(screen.getByTestId("profile-decisions-stack").contains(row)).toBe(false);
      // Below md the floating layer is not displayed at all.
      expect(screen.getByTestId("profile-decisions-stack").className).toMatch(/(^| )hidden( |$)/);
      expect(row.className).toContain("md:hidden");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(within(slot).getByTestId("profile-decisions-bar-row")).toBeInTheDocument();

      expect(within(slot).getByTestId("profile-decisions-bar-row-open")).toHaveAttribute(
        "href",
        "/profile#profile-health",
      );
      slot.remove();
    });
  });
});

// Property 10: the per-issue dismissal store degrades safely and accumulates
// ids rather than replacing them.
describe("gap-decisions-dismissed", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keys the storage entry per flow id", () => {
    expect(decisionsDismissedKey("flow-1")).toBe("applire.gaps.decisionsDismissed.flow-1.u.anonymous");
    expect(decisionsDismissedKey("flow-1")).not.toBe(decisionsDismissedKey("flow-2"));
  });

  it("keys the storage entry per signed-in user too (Strawberry D-9)", () => {
    setStorageUserId("user-a");
    const a = decisionsDismissedKey("flow-1");
    setStorageUserId("user-b");
    const b = decisionsDismissedKey("flow-1");
    setStorageUserId(null);
    expect(a).toBe("applire.gaps.decisionsDismissed.flow-1.u.user-a");
    expect(a).not.toBe(b);
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

  // Hazelnut b3 WP-V (UX audit, Felix): "Your answer touched…" right after an
  // IMPORT, with no answer given. The copy follows the decisions' origin.
  describe("copy by origin", () => {
    const fromSource = (thread: HealthIssue["thread"], id: string, src: string): HealthIssue =>
      thread === "conflict"
        ? { ...issue(thread, id), incoming_source: src }
        : { ...issue(thread, id), source_record_ref: src };

    it("classifies import, answer and mixed origins", () => {
      expect(decisionsOrigin([fromSource("confirmation", "c", "cv_upload")])).toBe("import");
      expect(decisionsOrigin([fromSource("conflict", "a", "linkedin_import"), fromSource("confirmation", "b", "cv_paste")])).toBe("import");
      expect(decisionsOrigin([fromSource("conflict", "a", "interview")])).toBe("answer");
      expect(decisionsOrigin([fromSource("conflict", "a", "interview"), fromSource("conflict", "b", "cv_upload")])).toBe("mixed");
      expect(decisionsOrigin([issue("conflict", "x")])).toBe("mixed");
    });

    it("says 'combining your documents' — never 'your answer' — after an import", async () => {
      global.fetch = mockFetch({ issues: [fromSource("confirmation", "c1", "cv_upload")] }) as typeof fetch;
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "en"));
      await flushEffects();
      const body = await screen.findByTestId("profile-decisions-body");
      expect(body).toHaveTextContent(/Combining your documents/);
      expect(body).not.toHaveTextContent(/Your answer/);
    });

    it("keeps 'your answer' for a decision an interview answer raised", async () => {
      global.fetch = mockFetch({ issues: [fromSource("conflict", "k1", "interview")] }) as typeof fetch;
      render(withIntl(<ProfileDecisionsCard apiBase="" flowId="flow-1" />, "de"));
      await flushEffects();
      expect(await screen.findByTestId("profile-decisions-body")).toHaveTextContent(/Deine Antwort/);
    });
  });
});
