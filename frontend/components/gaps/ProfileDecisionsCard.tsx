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
//    header); after the window or the × it shrinks to a compact chip that is
//    portaled into the top bar (F-2: a fixed chip below the header
//    covered the list once the page scrolled), and stays while decisions are
//    open;
//  * phone (< md): no floating overlay. The compact row is portaled into the
//    TOP of the page's fixed decision bar (`mobileSlot`), whose measured
//    spacer already keeps it off the content; it is always there.
// Hazelnut build 5 (ADR-099 cl. 10): the CTA LINKS to the profile health hub,
// where each decision is answered — the notice no longer opens its own copy of
// the review drawer (one condition, one place).
//
// Deliberately NOT a second conflict UI: a popup shows a count and one line.
//
// Derived from `/api/profile/health` rather than from the answer response, so a
// reload still shows it (the answer response is per-turn and is wiped on the
// next send — `interview/page.tsx:528`, the shape this exists to not repeat).
//
// **Auto-hidden is not dismissed.** The timer collapses the notice; it records
// nothing, so an untouched notice returns on the next mount. Only the ×
// remembers (per decision id), and even then the dispute stays parked, the
// compact entry point stays, and the profile page still shows it.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import { isRenderedIssue } from "@/lib/profile-health";
import { TOPBAR_NOTICE_SLOT_ID } from "@/components/shell/AppTopbar";
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

/**
 * Hazelnut b3 WP-V (UX audit 2026-10-20, Felix): the notice said "Your answer
 * touched something your profile already says" right after an IMPORT, before
 * any answer was given. The copy now follows where the open decisions came
 * from: a conflict's `incoming_source`, a confirmation's `source_record_ref`
 * (the record's `source`, `services/profile/health.py`).
 */
const IMPORT_SOURCES = new Set(["cv_upload", "cv_paste", "linkedin_import", "xing_import"]);
const ANSWER_SOURCES = new Set(["interview", "agent_interview", "testimony"]);

export type DecisionsOrigin = "import" | "answer" | "mixed";

export function decisionsOrigin(issues: HealthIssue[]): DecisionsOrigin {
  const origins = new Set(
    issues.map((i) => {
      const src = (i.thread === "conflict" ? i.incoming_source : i.source_record_ref) ?? "";
      return IMPORT_SOURCES.has(src) ? "import" : ANSWER_SOURCES.has(src) ? "answer" : "other";
    }),
  );
  if (origins.size === 1 && origins.has("import")) return "import";
  if (origins.size === 1 && origins.has("answer")) return "answer";
  return "mixed";
}

const BODY_KEY: Record<DecisionsOrigin, string> = {
  import: "decisions.bodyImport",
  answer: "decisions.body",
  mixed: "decisions.bodyMixed",
};

/**
 * Hazelnut build 5 (ADR-099 cl. 10 — one condition, one place): hub conflicts
 * and confirmations are answered in the profile health hub ONLY. This notice
 * counts them and links there; it no longer opens a second copy of the review
 * drawer over the gaps page.
 */
export const PROFILE_HEALTH_HREF = "/profile#profile-health";

export function decisionIssues(health: ProfileHealth | null): HealthIssue[] {
  if (!health?.issues) return [];
  return health.issues.filter((i) => DECISION_THREADS.has(i.thread) && isRenderedIssue(i));
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
  const tHealth = useTranslations("health");
  const [issues, setIssues] = useState<HealthIssue[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [autoHidden, setAutoHidden] = useState(false);
  const [autoSeconds, setAutoSeconds] = useState(DEFAULT_AUTO_DISMISS_SECONDS);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const known = useRef<Set<string>>(new Set());
  // Ruling F-2: on desktop the compact entry point lives in the top bar.
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setHeaderSlot(document.getElementById(TOPBAR_NOTICE_SLOT_ID));
  }, []);

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
    if (autoSeconds <= 0 || !expanded) return;
    timer.current = setTimeout(() => setAutoHidden(true), autoSeconds * 1000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [autoSeconds, expanded, undismissed.length]);

  // The × collapses the notice to its entry point and remembers the decisions
  // it was about — a new one brings the full notice back.
  const dismiss = () => {
    const ids = undismissed.map((i) => i.id);
    setDismissed((prev) => new Set([...prev, ...ids]));
    for (const id of ids) markDecisionDismissed(flowId, id);
  };

  const show = issues.length > 0;
  const link = (testId: string) => (
    <Button data-testid={testId} size="sm" variant="outline" asChild>
      <a href={PROFILE_HEALTH_HREF}>{tHealth("decisionsLink")}</a>
    </Button>
  );

  const compact = (testId: string, className: string) => (
    <div
      data-testid={testId}
      className={`flex items-center justify-between gap-3 rounded-lg border border-warning/40 border-l-4 border-l-warning bg-white px-3 py-2 ${className}`}
    >
      <p className="text-sm font-semibold text-neutral-dark">
        {t("decisions.compact", { count: issues.length })}
      </p>
      {link(`${testId}-open`)}
    </div>
  );

  const barRow = compact("profile-decisions-bar-row", "mb-3 md:hidden");
  const chip = compact("profile-decisions-compact", "pointer-events-auto py-1 shadow-sm");

  return (
    <>
      {/* Desktop: the V-1b corner (top-right, below the header bar). z-[65]
          sits above the shell sidebar (z-[60]) and below a full-screen dialog
          (z-[70]); hidden while the drawer is open. Below md this whole layer
          is gone — the phone gets the bar row instead. */}
      {show && (expanded || !headerSlot) && (
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
              <p data-testid="profile-decisions-body" className="mt-1 text-sm text-gray-600">
                {t(BODY_KEY[decisionsOrigin(issues)])}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                {link("profile-decisions-open")}
              </div>
              <p className="mt-2 text-xs leading-snug text-gray-500">
                {t("decisions.stillThere")}
              </p>
            </div>
          ) : (
            chip
          )}
        </div>
      )}

      {/* Desktop, collapsed: the chip sits in the top bar (F-2) — it
          covers nothing at any scroll position. */}
      {show && !expanded && headerSlot && createPortal(chip, headerSlot)}

      {/* Phone: the entry point lives in the page's fixed decision bar. */}
      {show && (mobileSlot ? createPortal(barRow, mobileSlot) : barRow)}

    </>
  );
}
