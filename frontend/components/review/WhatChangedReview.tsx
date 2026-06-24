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

"use client";

import { ArrowRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * US146 / ADR-040 — the single reusable "what changed & why" review surface.
 *
 * One component, four modes (extraction-confirm, merge-assumptions, interview-summary,
 * pre-download diff + attestation). It renders the structured decision trail
 * (FieldChange records from `GET /api/profile/changes`, or a generation diff) and
 * actively prompts the user — an animated review, not a passive list. Each row offers
 * a one-click correction entry (Branch G) via `onFix`.
 */

export type ReviewMode = "extraction" | "merge" | "interview" | "download";

export interface ReviewChange {
  section: string;
  field: string;
  action: "added" | "updated" | "merged";
  oldValue?: unknown;
  newValue?: unknown;
  /** English fallback / audit string (legacy records). */
  rationale?: string | null;
  /** Stable key localized via `review.rationale.<key>` (ADR-038). Preferred over `rationale`. */
  rationaleKey?: string | null;
}

export interface WhatChangedReviewProps {
  mode: ReviewMode;
  changes: ReviewChange[];
  /** Attestation / "looks good" — the active confirm that earns the detection credit. */
  onConfirm?: () => void;
  /** Skip / dismiss — keeps the flow un-gated (ADR-040 §4). */
  onDismiss?: () => void;
  /** Per-row correction entry point (Branch G). */
  onFix?: (change: ReviewChange) => void;
}

function titleKey(mode: ReviewMode): string {
  return {
    extraction: "titleExtraction",
    merge: "titleMerge",
    interview: "titleInterview",
    download: "titleDownload",
  }[mode];
}

function confirmKey(mode: ReviewMode): string {
  return mode === "download" ? "confirmDownload" : mode === "extraction" ? "confirmExtraction" : "confirmDefault";
}

function stringify(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function WhatChangedReview({ mode, changes, onConfirm, onDismiss, onFix }: WhatChangedReviewProps) {
  const t = useTranslations("review");

  return (
    <section
      data-testid="what-changed-review"
      data-mode={mode}
      className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur-sm"
    >
      <h3 data-testid="what-changed-title" className="text-base font-semibold text-foreground">
        {t(titleKey(mode))}
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">{t("subtitle")}</p>

      {changes.length === 0 ? (
        <p data-testid="what-changed-empty" className="mt-4 text-sm text-muted-foreground">
          {t("empty")}
        </p>
      ) : (
        <ul className="mt-4 space-y-3">
          {changes.map((change, i) => (
            <li
              key={`${change.section}-${change.field}-${i}`}
              data-testid="what-changed-row"
              className="flex items-start justify-between gap-3 rounded-lg border border-white/5 bg-white/5 p-3"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">{t(`section.${change.section}`)}</Badge>
                  <Badge variant="outline">{t(`action.${change.action}`)}</Badge>
                </div>
                {change.newValue != null && (
                  <p className="mt-1 flex items-center gap-2 text-sm">
                    {change.oldValue != null && stringify(change.oldValue) !== "" && (
                      <>
                        <span
                          data-testid="what-changed-oldvalue"
                          className="truncate text-muted-foreground line-through"
                        >
                          {stringify(change.oldValue)}
                        </span>
                        <ArrowRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </>
                    )}
                    <span className="truncate font-medium text-foreground">{stringify(change.newValue)}</span>
                  </p>
                )}
                {(change.rationaleKey || change.rationale) && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {change.rationaleKey ? t(`rationale.${change.rationaleKey}`) : change.rationale}
                  </p>
                )}
              </div>
              {onFix && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  data-testid="what-changed-fix"
                  onClick={() => onFix(change)}
                >
                  {t("fix")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {mode === "download" && (
        <p className="mt-4 text-sm text-muted-foreground">{t("attestationNote")}</p>
      )}

      <div className="mt-4 flex items-center justify-end gap-2">
        {onDismiss && (
          <Button type="button" variant="ghost" data-testid="what-changed-skip" onClick={onDismiss}>
            {t("skip")}
          </Button>
        )}
        {onConfirm && (
          <Button type="button" variant="primary" data-testid="what-changed-confirm" onClick={onConfirm}>
            {t(confirmKey(mode))}
          </Button>
        )}
      </div>
    </section>
  );
}
