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

import { useEffect, useId, useState } from "react";
import { useTranslations } from "next-intl";
import { patchApplicationStatus } from "@/lib/api/applications";
import { markSeenThisSession, seenThisSession } from "@/lib/session-once";

interface MarkAppliedPromptProps {
  applicationId: string;
  /** True when the application has no applied_at yet — the first submission. */
  stampAppliedAt: boolean;
  /**
   * The CV version that was just downloaded (E039/US219). When set, confirming
   * also pins it as the submitted version — the download prompt is the one
   * moment we know exactly which version went out, so capture it at zero cost.
   */
  submittedCvId?: string;
  onClose: () => void;
}

/**
 * Post-download natural-moment prompt (E039/US218, FMEA JF-E-P2.1):
 * a downloaded CV usually means the application is about to go out, so this
 * is the cheapest moment to keep the pipeline status truthful. A nudge, not
 * a gate — declining just closes it.
 *
 * Hazelnut build 3 (#789): asked ONCE per document set (this application) per
 * browser session — the PDF, the .docx and the letter of one application are
 * one sending, not three. A repeat mount closes itself without painting.
 */
export function MarkAppliedPrompt({ applicationId, stampAppliedAt, submittedCvId, onClose }: MarkAppliedPromptProps) {
  const t = useTranslations("applications");
  const [saving, setSaving] = useState(false);
  // Read once, at first render: a repeat must not flash the overlay.
  const [alreadyAsked] = useState(() => seenThisSession("mark-applied-prompt", applicationId));
  const titleId = useId();

  useEffect(() => {
    if (alreadyAsked) {
      onClose();
      return;
    }
    markSeenThisSession("mark-applied-prompt", applicationId);
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mount
  }, []);

  if (alreadyAsked) return null;

  async function handleConfirm() {
    setSaving(true);
    try {
      await patchApplicationStatus(applicationId, "applied", { stampAppliedAt, submittedCvId });
    } catch {
      // Best-effort nudge — a failed PATCH must not trap the user in the dialog.
    } finally {
      setSaving(false);
      onClose();
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      data-testid="mark-applied-prompt"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-w-sm w-full bg-white rounded-xl border border-outline-variant shadow-lg p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id={titleId} className="font-heading text-lg font-bold text-on-surface mb-2">
          {t("markAppliedTitle")}
        </h3>
        <p className="text-sm text-on-surface-variant mb-5">
          {t("markAppliedBody")}
          {submittedCvId && (
            <span className="block mt-2 text-xs text-on-surface-variant">
              {t("markAppliedPinHint")}
            </span>
          )}
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="text-sm font-bold px-4 py-2 rounded-lg text-on-surface-variant hover:bg-surface-container"
          >
            {t("markAppliedDecline")}
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={saving}
            className="text-sm font-bold px-4 py-2 rounded-lg bg-teal-dim text-white hover:bg-primary disabled:opacity-50"
          >
            {t("markAppliedConfirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
