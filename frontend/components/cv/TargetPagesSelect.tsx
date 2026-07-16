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

// E042/US239 (ADR-051 §1) — shared CV page-target control. Used by the flow
// CV step (per-generation override, never null) and the settings page
// (persisted default, null = "region standard"). A compact <select> — the
// option list is fixed to 2-5 pages because that's the realistic DACH CV
// range (2 = the norm, up to 5 for senior/academic profiles); a free-text
// spinner would let users pick implausible page counts the layout can't
// honestly hit.

import { useTranslations } from "next-intl";

const PAGE_OPTIONS = [2, 3, 4, 5] as const;

// Beyond this, the selector shows a hint that the choice exceeds the DACH norm.
const NORM_MAX_PAGES = 3;

export interface TargetPagesSelectProps {
  /** Currently selected page target. `null` only makes sense when `allowRegionStandard` is set. */
  value: number | null;
  onChange: (value: number | null) => void;
  /** Settings page only: offer a "region standard" (null) choice that clears the override. */
  allowRegionStandard?: boolean;
  className?: string;
}

export function TargetPagesSelect({
  value,
  onChange,
  allowRegionStandard = false,
  className,
}: TargetPagesSelectProps) {
  const t = useTranslations("cv");
  const showBeyondNormHint = value !== null && value > NORM_MAX_PAGES;

  return (
    <div className={className ?? "flex flex-col items-end gap-1"}>
      <label className="flex items-center gap-2 text-xs text-on-surface-variant" htmlFor="target-pages-select">
        {t("targetPagesLabel")}
        <select
          id="target-pages-select"
          data-testid="target-pages-select"
          value={value === null ? "region" : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "region" ? null : Number(raw));
          }}
          className="text-[12px] text-gray-600 border-[1.5px] border-gray-200 rounded-lg px-2.5 py-1 bg-white outline-none cursor-pointer"
        >
          {allowRegionStandard && (
            <option value="region">{t("targetPagesRegionStandardOption")}</option>
          )}
          {PAGE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n === 2 ? t("targetPagesStandardOption") : t("targetPagesOption", { count: n })}
            </option>
          ))}
        </select>
      </label>
      {showBeyondNormHint && (
        <p data-testid="target-pages-beyond-norm-hint" className="text-xs text-on-surface-variant">
          {t("targetPagesBeyondNormHint")}
        </p>
      )}
    </div>
  );
}
