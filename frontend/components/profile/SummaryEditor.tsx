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

// US292 (slice B) — structured editor for the Master Profile's
// `professional_summary` OBJECT section. Unlike the list editors
// (WorkExperienceEditor, CertificationsEditor, ...) this section is a single
// {de, en} pair, so the section-level PATCH is a merge-patch
// (`saveProfileObjectSection`, #178) rather than a whole-list replace, and
// this component renders ONLY the edit affordance + dialog — the read-only
// display stays ProfileSectionCard's `ProfileSectionBody` (unchanged).
//
// Finetuner-Delta FMEA JF-F-H3.1: both language slots are ALWAYS visible in
// the dialog, never behind a language selector — the slot Felix did not see
// must never be silently written.
//
// Legacy plain-string summaries (pre-#178 records) are treated as
// {de: null, en: null} with the string pre-filled into the current UI
// language's slot. Since the pre-filled text differs from the (null)
// baseline, an unmodified Save already carries that one slot in the patch —
// this is what migrates a legacy string into the structured shape the first
// time the section is opened and saved, no separate migration step needed.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { saveProfileObjectSection } from "@/lib/sectionSave";
import type { ProfileSectionsResponse } from "@/lib/profile-entries";
import type { SummaryValue, UiLanguage } from "./ProfileSectionCard";

interface SummaryDraft {
  de: string;
  en: string;
}

interface SummaryBaseline {
  de: string | null;
  en: string | null;
}

interface DialogState {
  draft: SummaryDraft;
  baseline: SummaryBaseline;
}

interface SummaryEditorProps {
  value: SummaryValue;
  uiLanguage: UiLanguage;
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

function computeBaseline(value: SummaryValue): SummaryBaseline {
  if (typeof value === "string" || !value) {
    // A legacy plain string (or no summary at all) has no per-language slots
    // on the backend yet — the baseline is functionally {de:null,en:null}.
    return { de: null, en: null };
  }
  return { de: value.de ?? null, en: value.en ?? null };
}

/** Trimmed-text comparison; undefined/null baseline and an empty draft both mean "no change". */
function slotChanged(draftText: string, baselineText: string | null): boolean {
  const trimmed = draftText.trim();
  if (baselineText === null || baselineText === undefined) return trimmed !== "";
  return trimmed !== baselineText.trim();
}

export function SummaryEditor({
  value,
  uiLanguage,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: SummaryEditorProps) {
  const t = useTranslations("profile");
  const tCommon = useTranslations("common");

  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [staleNotice, setStaleNotice] = useState(false);
  const [mismatchNotice, setMismatchNotice] = useState(false);

  const deFieldRef = useRef<HTMLTextAreaElement | null>(null);
  const inFlight = useRef(false);

  const dialogOpen = dialog !== null;
  useEffect(() => {
    if (dialogOpen) deFieldRef.current?.focus();
  }, [dialogOpen]);

  // F2 — a failed save (422) leaves focus stuck outside the dialog, so the
  // element-level onKeyDown never sees the Escape keystroke. A document-level
  // listener closes the gap without replacing the element-level handler.
  useEffect(() => {
    if (!dialogOpen) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") closeDialog();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [dialogOpen]);

  function openDialog() {
    setDialogError(null);
    setStaleNotice(false);
    const baseline = computeBaseline(value);
    const draft: SummaryDraft = { de: baseline.de ?? "", en: baseline.en ?? "" };
    if (typeof value === "string" && value.trim()) {
      draft[uiLanguage] = value;
    }
    setDialog({ draft, baseline });
  }

  function closeDialog() {
    setDialog(null);
    setDialogError(null);
    setStaleNotice(false);
  }

  function updateDraft(lang: "de" | "en", text: string) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, [lang]: text } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;

    const patch: Record<string, unknown> = {};
    (["de", "en"] as const).forEach((lang) => {
      if (slotChanged(dialog.draft[lang], dialog.baseline[lang])) {
        const trimmed = dialog.draft[lang].trim();
        patch[lang] = trimmed === "" ? null : trimmed;
      }
    });

    if (Object.keys(patch).length === 0) {
      closeDialog();
      return;
    }

    inFlight.current = true;
    setSaving(true);
    setDialogError(null);
    setStaleNotice(false);
    const result = await saveProfileObjectSection<ProfileSectionsResponse>({
      apiBase,
      section: "professional_summary",
      patch,
      basisUpdatedAt: profileUpdatedAt,
    });
    setSaving(false);
    inFlight.current = false;

    if (result.status === "ok") {
      onProfileUpdated(result.profile);
      setDialog(null);
      setMismatchNotice(result.mismatch);
      return;
    }
    if (result.status === "stale") {
      onProfileUpdated(result.current);
      setStaleNotice(true);
      return;
    }
    if (result.status === "invalid") {
      setDialogError(result.message || t("entryEditor.genericError"));
      return;
    }
    setDialogError(t("entryEditor.genericError"));
  }

  return (
    <div>
      {mismatchNotice && (
        <div
          data-testid="summary-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      <button
        type="button"
        data-testid="summary-edit"
        onClick={openDialog}
        className="mt-2 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("summaryEditor.editButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("summaryEditor.dialogTitle")}
            data-testid="summary-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("summaryEditor.dialogTitle")}</h3>
              <p className="mb-4 text-xs text-on-surface-variant">{t("summaryEditor.hint")}</p>

              <div className="space-y-3">
                <div>
                  <label htmlFor="summary-field-de" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("summaryEditor.fieldDe")}
                  </label>
                  <textarea
                    id="summary-field-de"
                    ref={deFieldRef}
                    data-testid="summary-field-de"
                    rows={5}
                    value={dialog.draft.de}
                    onChange={(e) => updateDraft("de", e.target.value)}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="summary-field-en" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("summaryEditor.fieldEn")}
                  </label>
                  <textarea
                    id="summary-field-en"
                    data-testid="summary-field-en"
                    rows={5}
                    value={dialog.draft.en}
                    onChange={(e) => updateDraft("en", e.target.value)}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                {staleNotice && (
                  <p
                    data-testid="summary-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="summary-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="summary-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="summary-save"
                  onClick={() => void handleSubmit()}
                  disabled={saving}
                  className="rounded-lg bg-primary px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? t("saving") : tCommon("save")}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
