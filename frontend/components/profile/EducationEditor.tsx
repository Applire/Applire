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

// US290 — structured editor for the Master Profile's education section,
// replacing the JSON textarea. Mirrors WorkExperienceEditor's dialog pattern;
// education has no tri-state current-position marker. A legacy `year` string
// (no start_date/end_date the picker can parse) is preserved verbatim and
// shown as the fallback period, exactly like the read-only card did.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  type EducationEntry,
  formatEntryPeriod,
  makeEmptyEducationEntry,
  nonEmptyText,
  type ProfileSectionsResponse,
} from "@/lib/profile-entries";
import { trimStringList } from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { PartialDateField } from "./PartialDateField";
import { BulletListField } from "./BulletListField";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: EducationEntry;
}

interface EducationEditorProps {
  entries: EducationEntry[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

function entryLabel(entry: EducationEntry): string {
  return [entry.degree, entry.institution].filter((s) => nonEmptyText(s)).join(" · ");
}

export function EducationEditor({ entries, apiBase, profileUpdatedAt, onProfileUpdated }: EducationEditorProps) {
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

  // Focus the first field when the dialog OPENS — keyed on open-state and
  // entry index, never on the `dialog` object itself: every keystroke
  // replaces that object, and `[dialog]` re-fired the effect on each one,
  // stealing focus back to the first field mid-word (adversarial finding
  // 2026-08-25, blocker).
  const dialogOpen = dialog !== null;
  const dialogIndex = dialog?.index ?? -1;
  useEffect(() => {
    if (dialogOpen) firstFieldRef.current?.focus();
  }, [dialogOpen, dialogIndex]);
  // Double-submit guard: `saving` is React state and lags a second click by a
  // render; a ref closes the gap (two identical PATCHes were observed).
  const inFlight = useRef(false);

  function openAdd() {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    setDialog({ index: null, draft: makeEmptyEducationEntry() });
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

  function updateDraft(patch: Partial<EducationEntry>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const draft = { ...dialog.draft, relevant_coursework: trimStringList(dialog.draft.relevant_coursework) };
    const institution = (draft.institution ?? "").trim();
    const degree = (draft.degree ?? "").trim();
    if (!institution || !degree) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }

    let nextEntries: EducationEntry[];
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
      section: "education",
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
      section: "education",
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

  return (
    <div>
      {mismatchNotice && (
        <div
          data-testid="education-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <div className="space-y-3">
          {entries.map((e, i) => {
            const heading = [e.degree, e.field].filter(nonEmptyText).join(", ");
            const period =
              formatEntryPeriod(e.start_date, e.end_date, t("present")) ??
              (nonEmptyText(e.year) ? e.year : null);
            const label = entryLabel(e) || t("notProvided");
            return (
              <div key={e.id ?? i} className="border-l-2 border-teal/40 pl-3">
                <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <p className="text-sm font-semibold text-neutral-dark">
                    {heading || e.institution || t("notProvided")}
                  </p>
                  <div className="flex items-center gap-2">
                    {period && <span className="text-xs text-gray-500">{period}</span>}
                    <button
                      type="button"
                      data-testid={`education-entry-edit-${i}`}
                      aria-label={t("entryEditor.editEntryAria", { label })}
                      onClick={() => openEdit(i)}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      data-testid={`education-entry-remove-${i}`}
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
                </div>
                {nonEmptyText(e.institution) && heading !== "" && (
                  <p className="text-xs text-gray-600">{e.institution}</p>
                )}
                {nonEmptyText(e.grade) && <p className="text-xs text-gray-500">{e.grade}</p>}
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        data-testid="education-add"
        onClick={openAdd}
        className="mt-3 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("educationEditor.addButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog.index === null ? t("educationEditor.dialogTitleAdd") : t("educationEditor.dialogTitleEdit")
            }
            data-testid="education-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null
                  ? t("educationEditor.dialogTitleAdd")
                  : t("educationEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="education-institution"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("educationEditor.fieldInstitution")}
                  </label>
                  <input
                    id="education-institution"
                    ref={firstFieldRef}
                    data-testid="education-field-institution"
                    value={dialog.draft.institution ?? ""}
                    onChange={(e) => updateDraft({ institution: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="education-degree" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("educationEditor.fieldDegree")}
                  </label>
                  <input
                    id="education-degree"
                    data-testid="education-field-degree"
                    value={dialog.draft.degree ?? ""}
                    onChange={(e) => updateDraft({ degree: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="education-field" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("educationEditor.fieldField")}
                  </label>
                  <input
                    id="education-field"
                    data-testid="education-field-field"
                    value={dialog.draft.field ?? ""}
                    onChange={(e) => updateDraft({ field: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <PartialDateField
                    id="education-start-date"
                    label={t("educationEditor.fieldStartDate")}
                    value={dialog.draft.start_date ?? null}
                    onChange={(v) => updateDraft({ start_date: v })}
                  />
                  <PartialDateField
                    id="education-end-date"
                    label={t("educationEditor.fieldEndDate")}
                    value={dialog.draft.end_date ?? null}
                    onChange={(v) => updateDraft({ end_date: v })}
                  />
                </div>

                <div>
                  <label htmlFor="education-grade" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("educationEditor.fieldGrade")}
                  </label>
                  <input
                    id="education-grade"
                    data-testid="education-field-grade"
                    value={dialog.draft.grade ?? ""}
                    onChange={(e) => updateDraft({ grade: e.target.value || null })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="education-thesis" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("educationEditor.fieldThesis")}
                  </label>
                  <input
                    id="education-thesis"
                    data-testid="education-field-thesis"
                    value={dialog.draft.thesis_title ?? ""}
                    onChange={(e) => updateDraft({ thesis_title: e.target.value || null })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <BulletListField
                  id="education-coursework"
                  label={t("educationEditor.fieldCoursework")}
                  items={dialog.draft.relevant_coursework ?? []}
                  onChange={(items) => updateDraft({ relevant_coursework: items })}
                  addButtonLabel={t("educationEditor.addCoursework")}
                  itemAriaLabel={(i) => t("educationEditor.courseworkAria", { index: i + 1 })}
                  allowReorder={false}
                />

                {staleNotice && (
                  <p
                    data-testid="education-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="education-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="education-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="education-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="education-entry-save"
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
            data-testid="education-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemoveIndex(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">{t("entryEditor.removeEntryBody")}</p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="education-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="education-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemoveIndex(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="education-entry-remove-confirm"
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
