"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Hazelnut build 5 (UAT-1, U-3/U-3b; ADR-099 cl. 11) — one open conflict as a
// card with its closed answer set. The outcomes are the backend's
// (`current_conflict.outcomes`: list → keep current · take new · keep both ·
// combine; a role entry's scalar → three; other scalars → two). "Combine" asks
// the model for ONE sentence built only from the two items; the candidate
// confirms or edits it before anything is written (no write without
// confirmation), and a proposal that failed the fact check removes the option.
//
// Values are quoted with the reader's own quotation marks and wrap in full —
// U-3b: English '…' inside German text and a chip cut off mid-word.

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { conflictSourceLabel, describeConflict } from "@/lib/conflict-display";
import {
  type ConflictOutcome,
  type CurrentConflict,
  visibleOutcomes,
} from "@/lib/profile-review";

/** A role entry's "keep both" is "two different positions" — the F4/#73 words. */
const ROLE_ENTRY_SECTIONS = new Set(["work_experience", "volunteer_activities"]);

export interface ConflictCardProps {
  conflict: CurrentConflict;
  busy: boolean;
  /** The merge proposal failed its fact check for THIS conflict. */
  mergeUnavailable: boolean;
  onOutcome: (outcome: Exclude<ConflictOutcome, "merge">) => void;
  /** Ask for a proposal; resolves to the text, or null when none passed the check. */
  onRequestMerge: () => Promise<string | null>;
  onConfirmMerge: (text: string) => void;
}

export function ConflictCard({
  conflict,
  busy,
  mergeUnavailable,
  onOutcome,
  onRequestMerge,
  onConfirmMerge,
}: ConflictCardProps) {
  const t = useTranslations("profileReview");
  const tHealth = useTranslations("health");
  const tProfile = useTranslations("profile");
  const [mergeText, setMergeText] = useState<string | null>(null);
  const [proposing, setProposing] = useState(false);

  const heading = describeConflict(conflict, tHealth, tProfile).heading;
  const source =
    conflictSourceLabel(tProfile, conflict.incoming_source) || tHealth("conflictSourceUnknown");
  const isRoleScalar =
    conflict.shape === "scalar" && ROLE_ENTRY_SECTIONS.has(conflict.section ?? "");
  const outcomes = visibleOutcomes(conflict, mergeUnavailable);

  const startMerge = async () => {
    setProposing(true);
    try {
      const proposal = await onRequestMerge();
      if (proposal !== null) setMergeText(proposal);
    } finally {
      setProposing(false);
    }
  };

  const label = (o: ConflictOutcome) =>
    o === "keep_both" && isRoleScalar ? t("keepBoth") : t(`outcome.${o}`);
  const hint = (o: ConflictOutcome) => {
    if (o === "keep_both" && isRoleScalar) return t("keepBothHint");
    // A scalar's two values replace each other; the list hints talk of points.
    return conflict.shape === "list" ? t(`outcomeHint.${o}`) : null;
  };

  return (
    <div
      data-testid="conflict-card"
      className="flex flex-col gap-3 rounded-xl border border-outline-variant bg-white p-4"
    >
      <span className="w-fit rounded-full bg-warning-container px-2 py-0.5 text-[11px] font-semibold text-on-surface">
        {tHealth("threadConflict")}
      </span>
      <p className="text-sm font-semibold text-on-surface" data-testid="conflict-card-heading">
        {heading}
      </p>

      {mergeText === null ? (
        <>
          <p className="text-sm text-on-surface-variant">
            {conflict.shape === "list"
              ? t("conflictIntro", { source })
              : t("conflictIntroScalar", { source })}
          </p>
          <div className="rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant">
              {t("currentLabel")}
            </p>
            <p data-testid="conflict-card-current" className="break-words">
              {t("quoted", { value: conflict.existing_value_display ?? "" })}
            </p>
          </div>
          <div className="rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant">
              {t("incomingLabel", { source })}
            </p>
            <p data-testid="conflict-card-incoming" className="break-words">
              {t("quoted", { value: conflict.incoming_value_display ?? "" })}
            </p>
          </div>
          {mergeUnavailable && conflict.outcomes.includes("merge") && (
            <p
              data-testid="conflict-merge-unavailable"
              className="rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-sm text-on-surface"
            >
              {t("mergeUnavailable")}
            </p>
          )}
          <div className="flex flex-col gap-2">
            {outcomes.map((o) => {
              const h = hint(o);
              return (
                <Button
                  key={o}
                  type="button"
                  variant="outline"
                  data-testid={`conflict-outcome-${o}`}
                  disabled={busy || proposing}
                  className="h-auto min-h-10 w-full flex-col items-start justify-start gap-0.5 whitespace-normal py-2 text-left"
                  onClick={() => (o === "merge" ? void startMerge() : onOutcome(o))}
                >
                  <span className="text-sm font-semibold text-on-surface">
                    {o === "merge" && proposing ? t("mergeLoading") : label(o)}
                  </span>
                  {h && <span className="text-xs font-normal text-on-surface-variant">{h}</span>}
                </Button>
              );
            })}
          </div>
        </>
      ) : (
        <div data-testid="conflict-merge-step" className="flex flex-col gap-3">
          <p className="text-sm font-semibold text-on-surface">{t("mergeTitle")}</p>
          <p className="text-sm text-on-surface-variant">{t("mergeLead")}</p>
          <label className="sr-only" htmlFor="conflict-merge-text">
            {t("mergeTextLabel")}
          </label>
          <textarea
            id="conflict-merge-text"
            data-testid="conflict-merge-text"
            className="min-h-[96px] w-full resize-y rounded-md border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20"
            value={mergeText}
            onChange={(e) => setMergeText(e.target.value)}
            disabled={busy}
          />
          <p className="text-xs text-on-surface-variant">{t("mergeKeepsOriginals")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              data-testid="conflict-merge-confirm"
              disabled={busy || mergeText.trim() === ""}
              onClick={() => onConfirmMerge(mergeText.trim())}
            >
              {t("mergeConfirm")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              data-testid="conflict-merge-back"
              disabled={busy}
              onClick={() => setMergeText(null)}
            >
              {t("mergeBack")}
            </Button>
          </div>
          <details className="text-xs text-on-surface-variant">
            <summary>{t("mergeSources")}</summary>
            <p className="mt-1.5 rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface">
              {t("quoted", { value: conflict.existing_value_display ?? "" })}
            </p>
            <p className="mt-1.5 rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface">
              {t("quoted", { value: conflict.incoming_value_display ?? "" })}
            </p>
          </details>
        </div>
      )}
    </div>
  );
}
