"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

// #737 (WP-E) — ONE confirmation in front of every path that makes a new
// generated document (same template, other template; the language switch keeps
// its own US289 notice). A new version is a new GeneratedCV / letter row:
// section edits are not carried (Finetuner exit conditions, JF-F-G2.1), and
// the current version stays listed under Dokumente. The notice names the
// edited sections — the cost is stated at the point of decision (principle 10).
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";

interface NewVersionDialogProps {
  open: boolean;
  /** Translated labels of the sections that carry the user's edit. */
  editedSections: string[];
  onConfirm: () => void;
  onCancel: () => void;
}

export function NewVersionDialog({ open, editedSections, onConfirm, onCancel }: NewVersionDialogProps) {
  const t = useTranslations("editTab");
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("confirmTitle")}
      data-testid="edit-new-version-dialog"
      className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
    >
      <div className="bg-white rounded-t-2xl md:rounded-xl p-6 shadow-xl w-full md:max-w-md">
        <h3 className="text-base font-bold text-on-surface mb-2">{t("confirmTitle")}</h3>
        <p className="text-sm text-on-surface-variant mb-3 leading-relaxed">{t("newVersionIntro")}</p>
        {editedSections.length > 0 && (
          <p
            data-testid="edit-new-version-loss"
            className="mb-4 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm leading-relaxed text-on-surface"
          >
            {t("newVersionLoss", { count: editedSections.length, sections: editedSections.join(", ") })}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            data-testid="edit-new-version-cancel"
            className="text-[13px] font-bold px-4 py-2 rounded-lg border border-outline-variant text-on-surface hover:bg-surface-container"
          >
            {t("confirmCancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="edit-new-version-confirm"
            className="text-[13px] font-bold px-4 py-2 rounded-lg bg-primary text-white hover:opacity-90"
          >
            {t("confirmGo")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
