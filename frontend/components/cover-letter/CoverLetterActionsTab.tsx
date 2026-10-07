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
  /**
   * #737 (RULING E-1 = A): what feeds the next version — the pin fates of this
   * letter. Slotted by the page; rendered inside "Neue Fassung erstellen".
   */
  pins?: ReactNode;
}

/**
 * Letter "Aktionen" (#737, RULING E-1 = A): one block, "Neue Fassung erstellen",
 * holding everything that makes a NEW letter (regenerate, language switch) and
 * what feeds it (pins). The page puts its one confirmation in front of the
 * regenerate when the body carries an edit.
 */
export function CoverLetterActionsTab({
  onRegenerateCoverLetter,
  languageSwitch,
  pins,
}: CoverLetterActionsTabProps) {
  const t = useTranslations("coverLetter");
  const tEdit = useTranslations("editTab");
  return (
    <div className="flex flex-col gap-3 p-3" data-testid="cl-new-version">
      <p className="text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
        {tEdit("newVersionTitle")}
      </p>
      <p className="text-xs text-on-surface-variant">{tEdit("newVersionIntro")}</p>
      <button
        type="button"
        onClick={onRegenerateCoverLetter}
        className="btn-glass w-full justify-center inline-flex items-center gap-2"
        data-testid="cl-regenerate-btn"
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
        <span aria-hidden="true">↻</span>
        {t("regenerate")}
      </button>
      {languageSwitch && (
        <div className="rounded-lg border border-outline-variant bg-surface-container/50 px-3 py-2">
          {languageSwitch}
        </div>
      )}
      {pins && (
        <div className="flex flex-col gap-2" data-testid="cl-new-version-pins">
          <p className="text-xs text-on-surface-variant">{tEdit("pinsLead")}</p>
          {pins}
        </div>
      )}
    </div>
  );
}
