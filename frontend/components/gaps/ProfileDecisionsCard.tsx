"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #686 (JF-M-3.5) — a profile dispute raised from a gap answer reaches the user.
//
// The gaps page's per-gap answer POSTs `/api/session/{id}/message`, checks
// `res.ok` and closes the card; `SessionMessageResponse.pending_conflicts` /
// `.pending_confirmations` were discarded and nothing in the flow pointed at
// the dispute the answer had raised. The founder found one days later by
// opening the profile page (UAT 2026-09-06).
//
// **Shape: ONE notice and a persistent entry point (M4, UX audit 2026-10-20,
// founder ruling UX-2 — amends V-1/V-1b of 2026-09-09).** V-1 asked for one
// popup per pending decision, up to three, then "+N more", auto-hidden after
// `notice_auto_dismiss_seconds`. Each popup carried the TOTAL count and the
// same body, so a Pixel 7 showed three identical notices over ~2/3 of the gaps
// page for 30 s on every visit — and the auto-hide took the only "decide now"
// with it. Now:
//  * desktop (md+): one notice in the V-1b corner (top-right, below the
//    header, over the job-ad card's empty right part); after the window or
//    the × it shrinks to a compact chip in the same corner, which stays while
//    decisions are open;
//  * phone (< md): no floating overlay. The compact row is portaled into the
//    TOP of the page's fixed decision bar (`mobileSlot`), whose measured
//    spacer already keeps it off the content; it is always there.
// "Decide now" opens the first open decision in the drawer; closing it
// re-reads the count, so the next one is offered.
//
// Deliberately NOT a second conflict UI: a popup shows a count and one line,
// and its CTA opens `ProfileReviewDrawer` with the same `HealthIssue` shape the
// profile page builds. The composition is `lib/conflict-display.ts`, shared
// with the Health hub and the interview page's `ConflictCard`.
//
// Derived from `/api/profile/health` rather than from the answer response, so a
// reload still shows it (the answer response is per-turn and is wiped on the
// next send — `interview/page.tsx:528`, the shape this exists to not repeat).
//
// **Auto-hidden is not dismissed.** The timer collapses the notice; it records
// nothing, so an untouched notice returns on the next mount. Only the ×
// remembers (per decision id), and even then the dispute stays parked, the
// compact entry point stays, and the profile page still shows it.

import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProfileReviewDrawer } from "@/components/profile/ProfileReviewDrawer";
import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import {
  dismissedDecisions,
  markDecisionDismissed,
} from "@/lib/gap-decisions-dismissed";

/** The two `HealthThread` values that represent a decision the candidate owes. */
const DECISION_THREADS = new Set(["conflict", "confirmation"]);

/**
 * Fallback for `AppSettings.notice_auto_dismiss_seconds` until WP-O2's
 * `NOTICE_AUTO_DISMISS_SECONDS` lands in `config.py`, the settings registry and
 * the public settings payload. **0 means never auto-hide** — an operator who
 * sets it to 0 gets popups that wait for a decision.
 */
export const DEFAULT_AUTO_DISMISS_SECONDS = 30;

export function decisionIssues(health: ProfileHealth | null): HealthIssue[] {
  if (!health?.issues) return [];
  return health.issues.filter((i) => DECISION_THREADS.has(i.thread));
}

export interface ProfileDecisionsCardProps {
  apiBase: string;
  /** Dismissal is scoped to one flow visit, so it is keyed by the flow id. */
  flowId: string;
  /**
   * Bumped by the page after every answered gap, so a dispute raised by THIS
   * answer appears without a reload. Also re-read on mount.
   */
  refreshToken?: number;
  /**
   * Phone (< md): the element at the top of the page's fixed decision bar the
   * compact row is portaled into, so it never floats over the content. Absent
   * (null), the row renders in place at the mount point, below md only.
   */
  mobileSlot?: HTMLElement | null;
}

export function ProfileDecisionsCard({
  apiBase,
  flowId,
  refreshToken = 0,
  mobileSlot = null,
}: ProfileDecisionsCardProps) {
  const t = useTranslations("gaps");
  const [issues, setIssues] = useState<HealthIssue[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [autoHidden, setAutoHidden] = useState(false);
  const [drawerIssue, setDrawerIssue] = useState<HealthIssue | null>(null);
  const [autoSeconds, setAutoSeconds] = useState(DEFAULT_AUTO_DISMISS_SECONDS);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const known = useRef<Set<string>>(new Set());

  useEffect(() => {
    setDismissed(dismissedDecisions(flowId));
  }, [flowId]);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/profile/health`);
      if (!res.ok) return;
      const next = decisionIssues((await res.json()) as ProfileHealth);
      setIssues(next);
      // A decision not seen before has something new to say, so a previous
      // auto-hide lapses; re-reading the same ones (after the drawer) does not.
      if (next.some((i) => !known.current.has(i.id))) setAutoHidden(false);
      for (const i of next) known.current.add(i.id);
    } catch {
      // A health read that fails leaves the stack hidden. The dispute is not
      // lost — it is on the profile page, which is what the popup says anyway.
    }
  }, [apiBase]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  // The operator's auto-dismiss window. Read from the settings payload, with a
  // fallback until WP-O2's field lands; an absent or non-numeric value is the
  // fallback, never 0, because 0 is a deliberate "never hide" and must be said.
  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const res = await fetch(`${apiBase}/api/settings`);
        if (!res.ok || !live) return;
        const seconds = (await res.json())?.notice_auto_dismiss_seconds;
        if (live && typeof seconds === "number" && seconds >= 0) setAutoSeconds(seconds);
      } catch {
        /* keep the fallback */
      }
    })();
    return () => {
      live = false;
    };
  }, [apiBase]);

  // The notice is full while some open decision has not been waved away; the
  // entry point (chip / bar row) stays as long as ANY decision is open.
  const undismissed = useMemo(
    () => issues.filter((i) => !dismissed.has(i.id)),
    [issues, dismissed],
  );
  const expanded = !autoHidden && undismissed.length > 0;

  // The timer. Restarted whenever what is on screen changes — the founder's
  // wording is "x seconds without interaction".
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (autoSeconds <= 0 || !expanded || drawerIssue) return;
    timer.current = setTimeout(() => setAutoHidden(true), autoSeconds * 1000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [autoSeconds, expanded, undismissed.length, drawerIssue]);

  // The × collapses the notice to its entry point and remembers the decisions
  // it was about — a new one brings the full notice back.
  const dismiss = () => {
    const ids = undismissed.map((i) => i.id);
    setDismissed((prev) => new Set([...prev, ...ids]));
    for (const id of ids) markDecisionDismissed(flowId, id);
  };

  const open = () => setDrawerIssue(undismissed[0] ?? issues[0] ?? null);
  const show = !drawerIssue && issues.length > 0;

  const compact = (testId: string, className: string) => (
    <div
      data-testid={testId}
      className={`flex items-center justify-between gap-3 rounded-lg border border-warning/40 border-l-4 border-l-warning bg-white px-3 py-2 ${className}`}
    >
      <p className="text-sm font-semibold text-neutral-dark">
        {t("decisions.compact", { count: issues.length })}
      </p>
      <Button data-testid={`${testId}-open`} size="sm" variant="outline" onClick={open}>
        {t("decisions.cta")}
      </Button>
    </div>
  );

  const barRow = compact("profile-decisions-bar-row", "mb-3 md:hidden");

  return (
    <>
      {/* Desktop: the V-1b corner (top-right, below the header bar). z-[65]
          sits above the shell sidebar (z-[60]) and below a full-screen dialog
          (z-[70]); hidden while the drawer is open. Below md this whole layer
          is gone — the phone gets the bar row instead. */}
      {show && (
        <div
          data-testid="profile-decisions-stack"
          className="pointer-events-none fixed right-4 top-20 z-[65] hidden w-[22rem] md:block"
        >
          {expanded ? (
            <div
              data-testid="profile-decisions-card"
              // OPAQUE: a floating notice sits over arbitrary content; `bg-white`
              // is the solid-surface convention (`MergeGateDialog`), the warning
              // role stays as the border and the left accent.
              className="pointer-events-auto rounded-lg border border-warning/40 border-l-4 border-l-warning bg-white px-4 py-3 shadow-lg"
            >
              <div className="flex items-start justify-between gap-3">
                <p
                  data-testid="profile-decisions-title"
                  className="text-sm font-semibold text-neutral-dark"
                >
                  {t("decisions.title", { count: issues.length })}
                </p>
                <Button
                  data-testid="profile-decisions-dismiss"
                  size="sm"
                  variant="ghost"
                  aria-label={t("decisions.dismissAria")}
                  onClick={dismiss}
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
              <p className="mt-1 text-sm text-gray-600">{t("decisions.body")}</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Button
                  data-testid="profile-decisions-open"
                  size="sm"
                  variant="outline"
                  onClick={open}
                >
                  {t("decisions.cta")}
                </Button>
              </div>
              <p className="mt-2 text-xs leading-snug text-gray-500">
                {t("decisions.stillThere")}
              </p>
            </div>
          ) : (
            compact("profile-decisions-compact", "pointer-events-auto shadow-lg")
          )}
        </div>
      )}

      {/* Phone: the entry point lives in the page's fixed decision bar. */}
      {show && (mobileSlot ? createPortal(barRow, mobileSlot) : barRow)}

      <ProfileReviewDrawer
        open={drawerIssue !== null}
        issue={drawerIssue}
        onClose={() => {
          setDrawerIssue(null);
          // Re-read rather than assume: the walk may have resolved some, all, or
          // none of them. When nothing is left the stack disappears on its own —
          // nothing here decides that it did.
          void load();
        }}
        onAction={() => {
          // The drawer's "fix it in the section" action has no section to scroll
          // to on this page. Send the candidate to the profile page, where the
          // affected section and its editor live — never a dead end (F3b).
          setDrawerIssue(null);
          if (typeof window !== "undefined") window.location.href = "/profile";
        }}
      />
    </>
  );
}
