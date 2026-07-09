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

// E039/US221 (journey Branch H) — "Your profile grew since this CV was
// generated." A nudge, never a gate: it explains WHAT the profile gained (the
// re-tailor must explain the change or the new version erodes trust), offers
// one-click re-tailor through the existing generation pipeline, and a
// persisted dismiss. It never auto-regenerates and never touches a pinned
// submitted version.

import { useTranslations } from "next-intl";
import { formatGained, type StaleCVGained } from "@/lib/stale-cv";

// Non-user-facing Material Symbols identifier — JS const to avoid the JSX literal rule
const GROWTH_ICON = "trending_up";

export interface StaleCvBannerProps {
  gained: StaleCVGained[];
  /** False when the application has no flow session to land the new version in. */
  canRetailor: boolean;
  /** True while the re-tailor POST is in flight — prevents double-clicks. */
  retailoring: boolean;
  onRetailor: () => void;
  onDismiss: () => void;
}

export function StaleCvBanner({
  gained,
  canRetailor,
  retailoring,
  onRetailor,
  onDismiss,
}: StaleCvBannerProps) {
  const t = useTranslations("applications");
  const tProfile = useTranslations("profile");
  const gainedText = formatGained(gained, tProfile);

  return (
    <div
      className="p-4 rounded-lg bg-primary-container/40 border border-primary/30"
      data-testid="stale-cv-banner"
    >
      <div className="flex items-start gap-3">
        <span
          className="material-symbols-outlined text-primary mt-0.5"
          aria-hidden="true"
          style={{ fontSize: 20 }}
        >
          {GROWTH_ICON}
        </span>
        <div className="flex-1">
          <p className="text-sm font-bold text-on-surface font-manrope">
            {t("staleCvTitle")}
          </p>
          <p className="text-sm text-on-surface mt-1 mb-3" data-testid="stale-cv-body">
            {gainedText
              ? t("staleCvBody", { gained: gainedText })
              : t("staleCvBodyPlain")}
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            {/* Nudge order: the re-tailor offer comes first, dismissal second —
                but dismiss is always available (never a gate). */}
            {canRetailor && (
              <button
                type="button"
                data-testid="stale-cv-retailor"
                onClick={onRetailor}
                disabled={retailoring}
                className="text-xs font-bold px-3 py-1.5 rounded-full bg-primary text-white hover:bg-teal-dim disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {retailoring ? t("staleCvRetailoring") : t("staleCvRetailor")}
              </button>
            )}
            <button
              type="button"
              data-testid="stale-cv-dismiss"
              onClick={onDismiss}
              className="text-xs font-bold px-3 py-1.5 rounded-full border border-outline-variant text-on-surface-variant bg-white hover:bg-surface-container"
            >
              {t("staleCvDismiss")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
