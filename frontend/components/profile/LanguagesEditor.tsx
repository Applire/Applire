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

// US291 — structured editor for the Master Profile's languages section,
// replacing the JSON textarea. Mirrors WorkExperienceEditor's dialog pattern.
// The backend field is `language`, NOT `name` (H2.4). `level` is free text on
// the backend; the UI offers a CEFR select (A1–C2, Native) but preserves any
// other legacy value verbatim as an extra option rather than discarding it.
// `status` is a read-only badge (H2.1); a NEW language is sent with an
// EXPLICIT `status: "confirmed"` (H2.2, PO ruling 2026-08-25).

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  makeEmptyLanguage,
  nonEmptyText,
  type Language,
  type ProfileSectionsResponse,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { StatusBadge } from "./StatusBadge";

const CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"] as const;
/** Matches the literal value already used in fixtures/legacy records. */
const NATIVE_LEVEL = "Native";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: Language;
}

interface LanguagesEditorProps {
  entries: Language[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

export function LanguagesEditor({ entries, apiBase, profileUpdatedAt, onProfileUpdated }: LanguagesEditorProps) {
  const t = useTranslations("profile");
  const tCommon = useTranslations("common");

  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [staleNotice, setStaleNotice] = useState(false);
  const [mismatchNotice, setMismatchNotice] = useState(false);
  const [pendingRemoveIndex, setPendingRemoveIndex] = useState<number | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const firstFieldRef = useRef<HTMLInputElement | null>(null);

  const dialogOpen = dialog !== null;
  const dialogIndex = dialog?.index ?? -1;
  useEffect(() => {
    if (dialogOpen) firstFieldRef.current?.focus();
  }, [dialogOpen, dialogIndex]);
  const inFlight = useRef(false);

  function openAdd() {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    setDialog({ index: null, draft: makeEmptyLanguage() });
  }

  function openEdit(index: number) {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    setDialog({ index, draft: cloneEntry(entries[index]) });
  }

  function closeDialog() {
    setDialog(null);
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
  }

  function updateDraft(patch: Partial<Language>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const language = (dialog.draft.language ?? "").trim();
    if (!language) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const draft = { ...dialog.draft, language };

    let nextEntries: Language[];
    let savedEntryId: string | undefined;
    if (index === null) {
      const { id: _unused, ...withoutId } = draft;
      void _unused;
      nextEntries = [...entries, withoutId];
    } else {
      nextEntries = entries.map((e, i) => (i === index ? draft : e));
      savedEntryId = draft.id;
    }

    inFlight.current = true;
    setSaving(true);
    setDialogError(null);
    setStaleNotice(false);
    const result = await saveProfileSection<ProfileSectionsResponse>({
      apiBase,
      section: "languages",
      entries: nextEntries,
      basisUpdatedAt: profileUpdatedAt,
      savedEntryId,
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

  async function confirmRemove() {
    if (pendingRemoveIndex === null) return;
    const nextEntries = entries.filter((_, i) => i !== pendingRemoveIndex);
    setRemoveBusy(true);
    setRemoveError(null);
    const result = await saveProfileSection<ProfileSectionsResponse>({
      apiBase,
      section: "languages",
      entries: nextEntries,
      basisUpdatedAt: profileUpdatedAt,
    });
    setRemoveBusy(false);

    if (result.status === "ok") {
      onProfileUpdated(result.profile);
      setPendingRemoveIndex(null);
      return;
    }
    if (result.status === "stale") {
      onProfileUpdated(result.current);
      setRemoveError(t("entryEditor.staleNotice"));
      return;
    }
    if (result.status === "invalid") {
      setRemoveError(result.message || t("entryEditor.genericError"));
      return;
    }
    setRemoveError(t("entryEditor.genericError"));
  }

  const draftLevel = dialog?.draft.level ?? "";
  const isKnownLevel =
    draftLevel === "" ||
    (CEFR_LEVELS as readonly string[]).includes(draftLevel) ||
    draftLevel === NATIVE_LEVEL;

  return (
    <div>
      {mismatchNotice && (
        <div
          data-testid="languages-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {entries.map((l, i) => {
            const label = nonEmptyText(l.language) ? l.language : t("notProvided");
            return (
              <div
                key={l.id ?? i}
                className="flex items-center gap-1.5 rounded-full border border-outline-variant bg-white px-3 py-1"
              >
                <span className="text-sm font-medium text-neutral-dark">{label}</span>
                {nonEmptyText(l.level) && <span className="text-xs text-gray-500">{l.level}</span>}
                <StatusBadge status={l.status} />
                <button
                  type="button"
                  data-testid={`language-edit-${i}`}
                  aria-label={t("entryEditor.editEntryAria", { label })}
                  onClick={() => openEdit(i)}
                  className="text-xs font-medium text-primary hover:underline"
                >
                  {t("edit")}
                </button>
                <button
                  type="button"
                  data-testid={`language-remove-${i}`}
                  aria-label={t("entryEditor.removeEntryAria", { label })}
                  onClick={() => {
                    setRemoveError(null);
                    setPendingRemoveIndex(i);
                  }}
                  className="text-xs font-medium text-critical hover:underline"
                >
                  {tCommon("delete")}
                </button>
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        data-testid="languages-add"
        onClick={openAdd}
        className="mt-3 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("entryEditor.addAsConfirmed")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog.index === null ? t("languagesEditor.dialogTitleAdd") : t("languagesEditor.dialogTitleEdit")
            }
            data-testid="language-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null
                  ? t("languagesEditor.dialogTitleAdd")
                  : t("languagesEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="language-name"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("languagesEditor.fieldLanguage")}
                  </label>
                  <input
                    id="language-name"
                    ref={firstFieldRef}
                    data-testid="language-field-language"
                    value={dialog.draft.language ?? ""}
                    onChange={(e) => updateDraft({ language: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="language-level"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("languagesEditor.fieldLevel")}
                  </label>
                  <select
                    id="language-level"
                    data-testid="language-field-level"
                    value={draftLevel}
                    onChange={(e) => updateDraft({ level: e.target.value || null })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  >
                    <option value="">{t("languagesEditor.levelUnset")}</option>
                    {CEFR_LEVELS.map((lvl) => (
                      <option key={lvl} value={lvl}>
                        {lvl}
                      </option>
                    ))}
                    <option value={NATIVE_LEVEL}>{t("languagesEditor.levelNative")}</option>
                    {!isKnownLevel && <option value={draftLevel}>{draftLevel}</option>}
                  </select>
                </div>

                {staleNotice && (
                  <p
                    data-testid="language-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="language-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="language-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="language-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="language-entry-save"
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

      {pendingRemoveIndex !== null &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("entryEditor.removeEntryTitle")}
            data-testid="language-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemoveIndex(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">{t("entryEditor.removeEntryBody")}</p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="language-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="language-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemoveIndex(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="language-entry-remove-confirm"
                  disabled={removeBusy}
                  onClick={() => void confirmRemove()}
                  className="rounded-lg bg-critical px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50"
                >
                  {tCommon("delete")}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
