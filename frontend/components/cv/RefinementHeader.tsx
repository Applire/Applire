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

import { useTranslations } from "next-intl";

export interface RefinementHeaderProps {
  roleTitle: string | null;
  matchScore: number | null;
  expiryWarning: { level: "none" | "warning" | "critical"; expiresIn: string } | null;
}

export function RefinementHeader({ roleTitle, matchScore, expiryWarning }: RefinementHeaderProps) {
  const t = useTranslations("cv");
  const showExpiry = expiryWarning && expiryWarning.level !== "none";
  const scorePct = matchScore !== null ? Math.round(matchScore * 100) : null;

  return (
    <div
      className="surface-glass flex items-center gap-3 px-3 py-2.5 border-b border-outline-variant"
      data-testid="refinement-header"
    >
      {scorePct !== null && (
        <div className="relative w-10 h-10 flex-shrink-0" data-testid="refinement-header-score">
          <svg
            className="w-full h-full -rotate-90"
            viewBox="0 0 36 36"
            role="img"
            aria-label={t("matchScoreAriaLabel", { score: scorePct })}
          >
            <circle cx="18" cy="18" r="15.9155" fill="none" stroke="var(--color-primary-container)" strokeWidth="3.5" />
            <circle
              cx="18"
              cy="18"
              r="15.9155"
              fill="none"
              stroke="var(--color-gold)"
              strokeWidth="3.5"
              strokeDasharray={`${scorePct}, 100`}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <span className="text-[11px] font-bold text-primary font-heading">{scorePct}%</span>
          </div>
        </div>
      )}

      <div className="min-w-0 flex-1">
        {roleTitle && (
          <p
            className="text-sm font-heading font-semibold text-on-surface truncate"
            data-testid="refinement-header-role"
            title={roleTitle}
          >
            {roleTitle}
          </p>
        )}
        {showExpiry && expiryWarning && (
          <span
            className={`inline-block mt-0.5 text-[10px] uppercase tracking-wide font-semibold rounded-full px-2 py-0.5 ${
              expiryWarning.level === "critical"
                ? "bg-critical-container text-critical"
                : "bg-warning-container text-warning"
            }`}
            data-testid="refinement-header-expiry"
          >
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            {(expiryWarning.level === "critical" ? t("statusHeaderExpired") : t("statusHeaderExpiresOn")) + " " + expiryWarning.expiresIn}
          </span>
        )}
      </div>
    </div>
  );
}
