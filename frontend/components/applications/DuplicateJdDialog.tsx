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

// E039/US220 — journey Branch F (duplicate JD / reposted job). Shown when a
// fresh analysis matches a job already in the user's pipeline. The existing
// application is offered FIRST; "continue as new" always stays available and
// dismissing is allowed — recognition, never a gate.

import { useTranslations, useLocale } from "next-intl";

export interface DuplicateOfHint {
  application_id: string;
  job_analysis_id: string;
  company_name: string | null;
  role_title: string | null;
  analyzed_at: string;
  matched_on: "job" | "source_url" | "text";
}

interface DuplicateJdDialogProps {
  hint: DuplicateOfHint;
  onOpenExisting: () => void;
  onContinueNew: () => void;
  onDismiss: () => void;
}

export function DuplicateJdDialog({
  hint,
  onOpenExisting,
  onContinueNew,
  onDismiss,
}: DuplicateJdDialogProps) {
  const t = useTranslations("applications");
  const locale = useLocale();

  const date = new Date(hint.analyzed_at).toLocaleDateString(locale);
  const body =
    hint.company_name || hint.role_title
      ? t("duplicateBody", {
          date,
          company: hint.company_name ?? "—",
          role: hint.role_title ?? "—",
        })
      : t("duplicateBodyPlain", { date });

  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid="duplicate-jd-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="bg-white rounded-xl p-6 shadow-xl max-w-md w-full relative">
        <div className="flex items-start gap-3 mb-2">
          <div className="w-10 h-10 rounded-full bg-blue-50 flex items-center justify-center flex-shrink-0">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
            <span aria-hidden="true" className="material-symbols-outlined text-primary text-[22px]" style={{ fontVariationSettings: "'FILL' 1" }}>content_copy</span>
          </div>
          <h3 className="text-base font-bold text-on-surface pt-1.5">{t("duplicateTitle")}</h3>
        </div>
        <p data-testid="duplicate-jd-body" className="text-sm text-on-surface-variant mb-5 leading-relaxed">
          {body}
        </p>

        <div className="flex flex-col gap-2">
          <button
            type="button"
            data-testid="duplicate-jd-open-existing"
            onClick={onOpenExisting}
            className="w-full bg-primary text-white font-semibold py-2.5 rounded-lg text-sm hover:opacity-90"
          >
            {t("duplicateOpenExisting", { date })}
          </button>
          <button
            type="button"
            data-testid="duplicate-jd-continue-new"
            onClick={onContinueNew}
            className="w-full border border-outline-variant text-on-surface font-semibold py-2.5 rounded-lg text-sm hover:bg-surface-container-low"
          >
            {t("duplicateContinueNew")}
          </button>
        </div>

        {/* Rendered last so the primary choice is the first button in DOM/tab
            order; visually pinned to the top-right corner. */}
        <button
          type="button"
          data-testid="duplicate-jd-dismiss"
          onClick={onDismiss}
          aria-label={t("duplicateDismiss")}
          className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container-low"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
          <span aria-hidden="true" className="material-symbols-outlined text-[20px]">close</span>
        </button>
      </div>
    </div>
  );
}
