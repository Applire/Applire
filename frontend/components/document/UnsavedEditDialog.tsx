"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

// #737 (WP-E) — leaving *Bearbeiten* with an unsaved section draft. The tab
// strip renders only the active body (RefinementSidebar), so a switch unmounts
// the editor and the draft is gone; Finetuner Branch B promises this question.
// Portalled to <body> — inside the sidebar a fixed overlay is transform-trapped
// (applire-i18n styling rule 5).
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";

interface UnsavedEditDialogProps {
  open: boolean;
  sectionLabel: string | null;
  busy?: boolean;
  failed?: boolean;
  /** Absent → no "save and switch" (the letter body editor has no save handle). */
  onSave?: () => void;
  onDiscard: () => void;
  onStay: () => void;
}

export function UnsavedEditDialog({ open, sectionLabel, busy, failed, onSave, onDiscard, onStay }: UnsavedEditDialogProps) {
  const t = useTranslations("editTab");
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("unsavedTitle")}
      data-testid="edit-unsaved-dialog"
      className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
    >
      <div className="bg-white rounded-t-2xl md:rounded-xl p-6 shadow-xl w-full md:max-w-md">
        <h3 className="text-base font-bold text-on-surface mb-2">{t("unsavedTitle")}</h3>
        <p className="text-sm text-on-surface-variant mb-4 leading-relaxed">
          {t("unsavedBody", { section: sectionLabel ?? "" })}
        </p>
        {failed && <p className="mb-3 text-[13px] font-semibold text-critical">{t("saveFailed")}</p>}
        <div className="flex flex-col gap-2">
          {onSave && (
          <button
            type="button"
            onClick={onSave}
            disabled={busy}
            data-testid="edit-unsaved-save"
            className="w-full rounded-lg bg-primary px-4 py-2.5 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50"
          >
            {t("unsavedSave")}
          </button>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onDiscard}
              disabled={busy}
              data-testid="edit-unsaved-discard"
              className="flex-1 rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-critical hover:bg-surface-container disabled:opacity-50"
            >
              {t("unsavedDiscard")}
            </button>
            <button
              type="button"
              onClick={onStay}
              disabled={busy}
              data-testid="edit-unsaved-stay"
              className="flex-1 rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
            >
              {t("unsavedStay")}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
