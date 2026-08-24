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


import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

interface CoverLetterActionsTabProps {
  onRegenerateCoverLetter: () => void;
  /** E054/US289: the post-generation language switch (DocumentLanguageSwitch),
   *  slotted by the page — mirrors CVActionsTab's slot. */
  languageSwitch?: ReactNode;
}

// PDF download is owned by the shared DocumentTopBar (E038); the Aktionen tab
// only carries regenerate — mirroring CVActionsTab so both documents' Aktionen
// tabs are structurally consistent.
export function CoverLetterActionsTab({
  onRegenerateCoverLetter,
  languageSwitch,
}: CoverLetterActionsTabProps) {
  const t = useTranslations("coverLetter");
  return (
    <div className="flex flex-col gap-3 p-3">
      {languageSwitch && (
        <div className="rounded-lg border border-outline-variant bg-surface-container/50 px-3 py-2">
          {languageSwitch}
        </div>
      )}
      <div className="border-t border-neutral-200 pt-3">
        <p className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-2">
          {t("regenerateHeading")}
        </p>
        <p className="text-xs text-neutral-400 mb-3">
          {t("regenerateHint")}
        </p>
        <button
          type="button"
          onClick={onRegenerateCoverLetter}
          className="w-full border border-neutral-300 text-sm py-2.5 rounded hover:border-neutral-500 transition-colors"
          data-testid="cl-regenerate-btn"
        >
          <span className="flex items-center justify-center gap-1">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <span aria-hidden="true">↻</span>
            {t("regenerate")}
          </span>
        </button>
      </div>
    </div>
  );
}
