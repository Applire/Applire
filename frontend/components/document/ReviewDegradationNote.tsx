// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";

import { degradationLines, type DegradedReviewStep } from "@/lib/review-degradation";

/**
 * ADR-081 cl. 9 amended 2026-10-22 (#786, ruling T-2 = A): a review step of the
 * writing loop that did not run is TOLD, above the verdict, on the desktop panel
 * and the phone sheet alike. It names the check in user words and what happened,
 * says the document is finished all the same, and offers the two exits: a new
 * version (the existing regenerate path, behind its own confirmation) or reading
 * that part yourself. It never says the document is wrong (SF-WRITE.57).
 */
export function ReviewDegradationNote({
  steps,
  onRegenerate,
}: {
  steps: DegradedReviewStep[] | null | undefined;
  onRegenerate?: () => void;
}) {
  const t = useTranslations("documentReview");
  const lines = degradationLines(steps);
  if (lines.length === 0) return null;
  return (
    <div
      data-testid="review-degradation"
      role="status"
      className="flex flex-col gap-2 rounded-lg border border-warning/40 bg-warning-container px-3 py-2.5 text-on-surface"
    >
      <p className="flex items-center gap-2 text-sm font-semibold">
        <AlertTriangle className="h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
        {t("degradationTitle", { count: lines.length })}
      </p>
      <ul className="flex flex-col gap-1 text-sm leading-snug">
        {lines.map((l) => (
          <li key={`${l.label}-${l.stage}`} data-testid={`review-degradation-${l.label}-${l.stage}`}>
            {l.stage === "review"
              ? t("degradationReview", {
                  check: t(`degradationCheck.${l.label}`),
                  reason: t(`degradationReason.${l.reason}`),
                })
              : t("degradationCorrection", { check: t(`degradationCheck.${l.label}`) })}
          </li>
        ))}
      </ul>
      <p className="text-sm leading-snug text-on-surface-variant">{t("degradationAdvice")}</p>
      {onRegenerate && (
        <button
          type="button"
          data-testid="review-degradation-regenerate"
          onClick={onRegenerate}
          className="inline-flex items-center gap-1.5 self-start rounded-lg border border-outline-variant bg-surface-bright px-3 py-1.5 text-sm font-semibold text-primary hover:bg-surface-container"
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          {t("degradationRegenerate")}
        </button>
      )}
    </div>
  );
}
