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
// **Shape: a toast stack, not a card in the page flow (founder ruling V-1,
// 2026-09-09).** No reserved UI space — the popups are anchored in a corner,
// stack, and disappear on their own after `notice_auto_dismiss_seconds` without
// interaction. One popup per pending decision, up to three, then a single
// "+N more". The first build put a full-width notice at the top of the gap
// list; that reserved space on a page the candidate is trying to work through,
// and was reversed.
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
// **Auto-hidden is not dismissed.** The timer hides the stack; it records
// nothing, so an untouched popup returns on the next mount. Only the ×
// remembers, and even then the dispute stays parked and stays on the profile
// page — the popup's small print says so.

import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

/** Founder ruling V-1: at most three popups, then one "+N more". */
export const MAX_POPUPS = 3;

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
   * Height in px of the page's own bottom-fixed CTA bar, so the stack sits
   * ABOVE it on mobile instead of on top of it. The gaps page already measures
   * this for its own spacer (`decisionBarHeight`); passing it is cheaper and
   * more honest than this component guessing a constant that would drift the
   * moment the bar gains a line of text.
   *
   * Only used below `md:` — the bar is `static` at `md:` and above, so the
   * stack's own `md:bottom-4` wins there.
   */
  bottomOffsetPx?: number;
}

export function ProfileDecisionsCard({
  apiBase,
  flowId,
  refreshToken = 0,
  bottomOffsetPx = 0,
}: ProfileDecisionsCardProps) {
  const t = useTranslations("gaps");
  const [issues, setIssues] = useState<HealthIssue[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [autoHidden, setAutoHidden] = useState(false);
  const [drawerIssue, setDrawerIssue] = useState<HealthIssue | null>(null);
  const [autoSeconds, setAutoSeconds] = useState(DEFAULT_AUTO_DISMISS_SECONDS);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setDismissed(dismissedDecisions(flowId));
  }, [flowId]);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/profile/health`);
      if (!res.ok) return;
      setIssues(decisionIssues((await res.json()) as ProfileHealth));
      // The stack has something new to say, so a previous auto-hide lapses.
      setAutoHidden(false);
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

  const visible = useMemo(
    () => issues.filter((i) => !dismissed.has(i.id)),
    [issues, dismissed],
  );

  // The timer. Restarted whenever what is on screen changes — the founder's
  // wording is "x seconds without interaction", so dismissing one popup gives
  // the remaining ones a fresh window rather than inheriting a spent one.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (autoSeconds <= 0 || visible.length === 0 || autoHidden || drawerIssue) return;
    timer.current = setTimeout(() => setAutoHidden(true), autoSeconds * 1000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [autoSeconds, visible.length, autoHidden, drawerIssue]);

  const dismiss = (issueId: string) => {
    setDismissed((prev) => new Set([...prev, issueId]));
    markDecisionDismissed(flowId, issueId);
  };

  const shown = visible.slice(0, MAX_POPUPS);
  const overflow = visible.length - shown.length;

  return (
    <>
      {/* Anchored in a corner, never in the page flow: bottom-right on desktop,
          full-width bottom on mobile. z-[65] sits above the shell sidebar
          (z-[60]) and below a full-screen dialog (z-[70]); the stack also hides
          itself while the drawer is open, so there is no layering contest.
          `pointer-events-none` on the container keeps the page clickable
          THROUGH the gaps between popups. */}
      {!autoHidden && !drawerIssue && shown.length > 0 && (
        <div
          data-testid="profile-decisions-stack"
          // The breakpoint is `md:` and not `sm:` deliberately: that is where the
          // gaps page's own CTA bar stops being `fixed`, so the two elements
          // change layout on the same line rather than overlapping in between.
          style={{ "--stack-bottom": `${bottomOffsetPx + 12}px` } as React.CSSProperties}
          className="pointer-events-none fixed inset-x-3 bottom-[var(--stack-bottom)] z-[65] flex flex-col gap-2 md:inset-x-auto md:right-4 md:bottom-4 md:w-[22rem]"
        >
          {shown.map((issue) => (
            <div
              key={issue.id}
              data-testid="profile-decisions-card"
              // OPAQUE. `bg-warning/30 + bg-warning/5` is the house shape for an
              // INLINE notice sitting on a known page background; a floating
              // popup sits over arbitrary content, and at 5 % alpha the page —
              // a score circle, a saturated CTA button — reads straight through
              // the text. Caught on the 390 px screenshot, invisible to the
              // a11y tree and to every unit test. `bg-white` is the convention
              // for a solid surface in this codebase (`MergeGateDialog`); the
              // warning role stays as the border and the left accent.
              className="pointer-events-auto rounded-lg border border-warning/40 border-l-4 border-l-warning bg-white px-4 py-3 shadow-lg"
            >
              <div className="flex items-start justify-between gap-3">
                <p
                  data-testid="profile-decisions-title"
                  className="text-sm font-semibold text-neutral-dark"
                >
                  {t("decisions.title", { count: visible.length })}
                </p>
                <Button
                  data-testid="profile-decisions-dismiss"
                  size="sm"
                  variant="ghost"
                  aria-label={t("decisions.dismissAria")}
                  onClick={() => dismiss(issue.id)}
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
                  onClick={() => setDrawerIssue(issue)}
                >
                  {t("decisions.cta")}
                </Button>
              </div>
              <p className="mt-2 text-xs leading-snug text-gray-500">
                {t("decisions.stillThere")}
              </p>
            </div>
          ))}
          {overflow > 0 && (
            <div
              data-testid="profile-decisions-more"
              className="pointer-events-auto rounded-lg border border-outline-variant bg-white px-4 py-2 text-xs text-gray-600 shadow-lg"
            >
              {t("decisions.more", { count: overflow })}
            </div>
          )}
        </div>
      )}

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
