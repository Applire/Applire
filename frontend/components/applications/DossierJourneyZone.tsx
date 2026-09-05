"use client";

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

import Link from "next/link";
import { useTranslations } from "next-intl";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface DossierJourneyZoneProps {
  flowSessionId: string;
  currentStep: string;
  workflowStatus: string;
}

/**
 * The four VISIBLE flow steps and their `flow.step*` label keys — the same
 * mapping the flow layout's own stepper uses (app/(shell)/flow/[flowId]/
 * layout.tsx `STEP_KEYS`). `jd_analysis` has no dot here (it resolves to the
 * flow index almost instantly and is never a step a user "returns to"), and
 * `complete` reuses the last (`cv_generation`) dot rather than adding a 5th.
 */
const STEP_ORDER = ["cv_import", "gap_analysis", "interview", "cv_generation"] as const;
type VisibleStep = (typeof STEP_ORDER)[number];

const STEP_LABEL_KEYS: Record<VisibleStep, string> = {
  cv_import: "stepProfile",
  gap_analysis: "stepGaps",
  interview: "stepInterview",
  cv_generation: "stepCV",
};

// workflow_status values where the active step is actually mid-generation —
// shows a spinner instead of a static highlight.
const BUSY_WORKFLOW_STATUSES = new Set(["analyzing", "interviewing", "cv_generating"]);

type MilestoneState = "done" | "active" | "pending";

/**
 * Journey zone (E041/US233) — a translated step strip that puts the user
 * back into the flow. Two distinct re-entry shapes:
 *  - Completed (`currentStep === "complete"`): the strip is all done
 *    milestones (never links — a past step isn't a route to revisit) plus
 *    two explicit result links: the CV result and the cover letter.
 *  - Mid-flow: the strip highlights the current step; the ONE navigation
 *    action goes to `/flow/{flowSessionId}` — the flow INDEX, never a
 *    hard-coded step route. Hard-coding a step desyncs the flow state
 *    machine (see `lib/flow-routing.ts` header comment and
 *    `DashboardApplicationCard.tsx` `handleAction`) — this is a locked
 *    boundary, not a style choice.
 */
export function DossierJourneyZone({ flowSessionId, currentStep, workflowStatus }: DossierJourneyZoneProps) {
  const t = useTranslations("applications");
  const tFlow = useTranslations("flow");

  const isComplete = currentStep === "complete";
  const currentIndex = STEP_ORDER.indexOf(currentStep as VisibleStep);
  const isBusy = BUSY_WORKFLOW_STATUSES.has(workflowStatus);

  return (
    <Card className="p-6" data-testid="dossier-journey-zone">
      <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
        {t("journeyZoneTitle")}
      </h2>

      {/* US230 (E040): at 390 px the four steps do not fit one row. Each `li` got
          ~80 px of a 107-122 px `whitespace-nowrap` chip, so the labels painted
          over each other ("3 Interview" ran into "4 CV") — found by the mobile
          lane's per-element overflow sweep, not by the body-width assertion,
          which cannot fire inside the shell's `overflow-hidden`. Below `sm` the
          strip WRAPS and the connector rules are hidden (they are decorative and
          already `aria-hidden`); the desktop rail is unchanged from `sm` up. */}
      <ol
        className="flex flex-wrap items-center gap-x-1.5 gap-y-2 sm:gap-x-0"
        data-testid="dossier-journey-strip"
      >
        {STEP_ORDER.map((step, idx) => {
          const state: MilestoneState = isComplete
            ? "done"
            : idx === currentIndex
              ? "active"
              : idx < currentIndex
                ? "done"
                : "pending";

          return (
            <li
              key={step}
              className="flex items-center flex-initial sm:flex-1 sm:min-w-0 last:flex-initial"
            >
              <span
                data-testid="dossier-journey-milestone"
                data-state={state}
                className={cn(
                  "inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-full whitespace-nowrap",
                  state === "done" && "bg-success/10 text-success",
                  state === "active" && "bg-primary text-white",
                  state === "pending" && "bg-surface-container text-on-surface-variant"
                )}
              >
                {state === "done" && <CheckCircle2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />}
                {state === "active" && isBusy && (
                  <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin" aria-hidden="true" />
                )}
                {tFlow(STEP_LABEL_KEYS[step])}
              </span>
              {idx < STEP_ORDER.length - 1 && (
                <span
                  className="hidden sm:block h-px flex-1 min-w-[16px] bg-outline-variant"
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-4 flex items-center gap-2 flex-wrap">
        {isComplete ? (
          <>
            <Link
              href={`/flow/${flowSessionId}/cv`}
              data-testid="dossier-journey-cv-link"
              className="text-xs font-bold px-3 py-1.5 rounded-lg bg-teal-dim text-white hover:bg-primary"
            >
              {t("openCvAction")}
            </Link>
            {/* Same destination as the documents zone's draft link
                (/flow/{id}/cover-letter) — reuses ITS label ("View draft"),
                not the header's "Cover letter" button, which opens the PDF
                directly. Same text + different behavior would be confusing. */}
            <Link
              href={`/flow/${flowSessionId}/cover-letter`}
              data-testid="dossier-journey-cl-link"
              className="text-xs font-bold px-3 py-1.5 rounded-lg bg-white border border-outline-variant text-on-surface hover:bg-surface-container"
            >
              {t("coverLetterDraftLink")}
            </Link>
          </>
        ) : (
          // Single re-entry action, index-only — see the boundary note above.
          <Link
            href={`/flow/${flowSessionId}`}
            data-testid="dossier-journey-resume-link"
            className="text-xs font-bold px-3 py-1.5 rounded-lg bg-teal-dim text-white hover:bg-primary"
          >
            {t("resumeAction")}
          </Link>
        )}
      </div>
    </Card>
  );
}
