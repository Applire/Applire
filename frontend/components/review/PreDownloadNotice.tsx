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

import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

/**
 * ADR-040 (amended 2026-07-04) — the pre-download notice.
 *
 * A plain "AI-generated content can have faults" notice with an optional
 * "Don't show this again" control (governed by the shared `hide_predownload_notice`
 * user setting, passed in as `canSuppress`). The former red-flag diff rows were
 * retired (Chocolate UAT: broken references, truncated text); a richer review
 * surface is a later-flavour redesign.
 *
 * `onConfirm(dontShowAgain)` proceeds with the download. A nudge, never a gate
 * (ADR-040 §4).
 */
export interface PreDownloadNoticeProps {
  /** The shared preference is not already set — offer the "don't show again" control. */
  canSuppress: boolean;
  onConfirm: (dontShowAgain: boolean) => void;
  onCancel: () => void;
}

export function PreDownloadNotice({ canSuppress, onConfirm, onCancel }: PreDownloadNoticeProps) {
  const t = useTranslations("predownload");
  const [dontShowAgain, setDontShowAgain] = useState(false);

  const showCheckbox = canSuppress;

  return (
    <section
      data-testid="predownload-notice"
      className="rounded-xl border border-outline-variant bg-white p-5 shadow-xl"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-on-surface">{t("title")}</h3>
          <p className="mt-1 text-sm text-on-surface-variant">{t("warning")}</p>
        </div>
      </div>

      {showCheckbox && (
        <label
          data-testid="predownload-dontshowagain"
          className="mt-4 flex cursor-pointer items-center gap-2 text-sm text-on-surface-variant"
        >
          <input
            type="checkbox"
            data-testid="predownload-dontshowagain-input"
            className="h-4 w-4 rounded border-outline-variant"
            checked={dontShowAgain}
            onChange={(e) => setDontShowAgain(e.target.checked)}
          />
          {t("dontShowAgain")}
        </label>
      )}

      <div className="mt-5 flex items-center justify-end gap-2">
        <Button type="button" variant="ghost" data-testid="predownload-cancel" onClick={onCancel}>
          {t("cancel")}
        </Button>
        <Button
          type="button"
          variant="primary"
          data-testid="predownload-download"
          onClick={() => onConfirm(showCheckbox ? dontShowAgain : false)}
        >
          {t("download")}
        </Button>
      </div>
    </section>
  );
}
