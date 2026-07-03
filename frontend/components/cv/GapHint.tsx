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

// frontend/components/cv/GapHint.tsx
"use client";

import { useTranslations } from "next-intl";

interface GapHintItem {
  id: string;
  label: string;
  // #117 (ADR-019/048): evidence status decides the CTA. "claimable" = profile-backed,
  // safe to write into the document; "honest" = not in the profile — the only truthful
  // fix is profile enrichment, never a written claim. Optional for back-compat.
  kind?: "claimable" | "honest";
}

interface GapHintProps {
  gap: GapHintItem;
  onDismiss: (gapId: string) => void;
  onAddressGap: (gapId: string) => void;
  onEnrichProfile?: (gapId: string) => void;
}

export function GapHint({ gap, onDismiss, onAddressGap, onEnrichProfile }: GapHintProps) {
  const t = useTranslations("cv");

  if (gap.kind === "honest") {
    return (
      <div className="mb-2">
        <div className="bg-warning-container border border-warning/30 rounded-lg px-3 py-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-neutral-dark font-medium">{gap.label}</span>
            <span
              className="text-[10px] uppercase tracking-wide text-gold-dim bg-gold-container px-1.5 py-0.5 rounded-full ml-2 shrink-0"
              data-testid="honest-gap-tag"
            >
              {t("honestGapTag")}
            </span>
          </div>
          <p className="text-xs text-neutral-dark mt-1">{t("honestGapHint")}</p>
          <div className="flex gap-1 mt-2">
            <button
              type="button"
              onClick={() => onDismiss(gap.id)}
              className="text-xs text-teal border border-teal px-2 py-0.5 rounded hover:bg-teal hover:text-white transition-colors"
              data-testid="dismiss-hint-btn"
            >
              {t("dismissHint")}
            </button>
            <button
              type="button"
              onClick={() => onEnrichProfile?.(gap.id)}
              className="text-xs text-white bg-teal border border-teal px-2 py-0.5 rounded hover:opacity-90 transition-opacity"
              data-testid="enrich-profile-btn"
            >
              {t("addViaProfileInterview")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mb-2">
      <div className="flex items-center justify-between bg-warning-container border border-warning/30 rounded-lg px-3 py-2">
        <span className="text-xs text-neutral-dark font-medium">{gap.label}</span>
        <div className="flex gap-1 ml-2 shrink-0">
          <button
            type="button"
            onClick={() => onDismiss(gap.id)}
            className="text-xs text-teal border border-teal px-2 py-0.5 rounded hover:bg-teal hover:text-white transition-colors"
            data-testid="write-myself-btn"
          >
            {t("writeMyself")}
          </button>
          <button
            type="button"
            onClick={() => onAddressGap(gap.id)}
            className="text-xs text-teal border border-teal px-2 py-0.5 rounded hover:bg-teal hover:text-white transition-colors"
            data-testid="kaile-help-btn"
          >
            {t("letKaileHelp")}
          </button>
        </div>
      </div>
    </div>
  );
}
