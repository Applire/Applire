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
// This card is the surfacing half. Deliberately NOT a second conflict UI: it
// shows a count and one line, and clicking it opens `ProfileReviewDrawer` with
// the same `HealthIssue` shape the profile page builds — the composition is
// `lib/conflict-display.ts`, shared with the Health hub and the interview
// page's `ConflictCard`.
//
// Derived from `/api/profile/health` rather than from the answer response, so
// a reload still shows it (the answer response is per-turn and is wiped on the
// next send — `interview/page.tsx:528`, the shape this card exists to not
// repeat). Dismissal (×) hides the note for this flow and resolves nothing:
// the dispute stays parked and stays on the profile page.

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProfileReviewDrawer } from "@/components/profile/ProfileReviewDrawer";
import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import {
  isDecisionsDismissed,
  markDecisionsDismissed,
} from "@/lib/gap-decisions-dismissed";

/** The two `HealthThread` values that represent a decision the candidate owes. */
const DECISION_THREADS = new Set(["conflict", "confirmation"]);

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
}

export function ProfileDecisionsCard({
  apiBase,
  flowId,
  refreshToken = 0,
}: ProfileDecisionsCardProps) {
  const t = useTranslations("gaps");
  const [issues, setIssues] = useState<HealthIssue[]>([]);
  const [dismissed, setDismissed] = useState(true); // hidden until storage is read
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setDismissed(isDecisionsDismissed(flowId));
  }, [flowId]);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/profile/health`);
      if (!res.ok) return;
      setIssues(decisionIssues((await res.json()) as ProfileHealth));
    } catch {
      // A health read that fails leaves the card hidden. The dispute is not
      // lost — it is on the profile page, which is what the card says anyway.
    }
  }, [apiBase]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const count = issues.length;
  if (count === 0 || dismissed) return null;

  const dismiss = () => {
    setDismissed(true);
    markDecisionsDismissed(flowId);
  };

  return (
    <>
      <div
        data-testid="profile-decisions-card"
        className="mb-6 rounded-lg border border-warning/30 bg-warning/5 px-4 py-3"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p
              data-testid="profile-decisions-title"
              className="text-sm font-semibold text-neutral-dark"
            >
              {t("decisions.title", { count })}
            </p>
            <p className="mt-1 text-sm text-gray-600">{t("decisions.body")}</p>
          </div>
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
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button
            data-testid="profile-decisions-open"
            size="sm"
            variant="outline"
            onClick={() => setDrawerOpen(true)}
          >
            {t("decisions.cta")}
          </Button>
          <p className="text-xs leading-snug text-gray-500">
            {t("decisions.stillThere")}
          </p>
        </div>
      </div>

      <ProfileReviewDrawer
        open={drawerOpen}
        issue={issues[0] ?? null}
        onClose={() => {
          setDrawerOpen(false);
          // Re-read rather than assume: the walk may have resolved some, all,
          // or none of them. When the count reaches 0 the card disappears on
          // its own — nothing here decides that it did.
          void load();
        }}
        onAction={() => {
          // The drawer's "fix it in the section" action has no section to
          // scroll to on this page. Send the candidate to the profile page,
          // which is where the affected section and its editor live — never a
          // dead end (F3b).
          setDrawerOpen(false);
          if (typeof window !== "undefined") window.location.href = "/profile";
        }}
      />
    </>
  );
}
