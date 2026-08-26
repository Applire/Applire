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

// US291 — structured editor for the Master Profile's skills section,
// replacing the JSON textarea. Mirrors WorkExperienceEditor's dialog pattern.
// `source` and `experience_refs` are provenance the backend derives — never a
// form control here, only spread through verbatim (H1.3). `status` is a
// read-only badge (H2.1): a `denied` skill can only be removed, never
// re-edited through this door; a NEW skill is sent with an EXPLICIT
// `status: "confirmed"` (H2.2, PO ruling 2026-08-25).

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  makeEmptySkill,
  nonEmptyText,
  type ProfileSectionsResponse,
  type Skill,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { StatusBadge } from "./StatusBadge";

const PROFICIENCY_VALUES = ["basic", "intermediate", "advanced", "expert"] as const;
const CATEGORY_VALUES = ["technical", "soft", "language", "domain"] as const;

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: Skill;
}

interface SkillsEditorProps {
  entries: Skill[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

export function SkillsEditor({ entries, apiBase, profileUpdatedAt, onProfileUpdated }: SkillsEditorProps) {
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
  // entry index, never on the `dialog` object itself (see WorkExperienceEditor
  // for the adversarial finding this guards against).
  const dialogOpen = dialog !== null;
  const dialogIndex = dialog?.index ?? -1;
  useEffect(() => {
    if (dialogOpen) firstFieldRef.current?.focus();
  }, [dialogOpen, dialogIndex]);
  // Double-submit guard: `saving` is React state and lags a second click by a
  // render; a ref closes the gap.
  const inFlight = useRef(false);

  function openAdd() {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    setDialog({ index: null, draft: makeEmptySkill() });
  }

  function openEdit(index: number) {
    if (entries[index]?.status === "denied") return;
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

  function updateDraft(patch: Partial<Skill>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const name = (dialog.draft.name ?? "").trim();
    const years = dialog.draft.years_experience;
    if (years !== null && years !== undefined && (!Number.isInteger(years) || years < 0 || years > 80)) {
      setValidationError(t("entryEditor.validationYearsRange"));
      return;
    }
    if (!name) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const draft = { ...dialog.draft, name };

    let nextEntries: Skill[];
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
      section: "skills",
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
      section: "skills",
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
          data-testid="skills-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {entries.map((s, i) => {
            const label = nonEmptyText(s.name) ? s.name : t("notProvided");
            const isDenied = s.status === "denied";
            return (
              <div
                key={s.id ?? i}
                className="flex items-center gap-1.5 rounded-full border border-outline-variant bg-white px-3 py-1"
              >
                <span className="max-w-[16rem] truncate text-sm font-medium text-neutral-dark" title={label}>{label}</span>
                {nonEmptyText(s.proficiency) && (
                  <span className="text-xs text-gray-500">
                    {PROFICIENCY_VALUES.includes(s.proficiency as (typeof PROFICIENCY_VALUES)[number])
                      ? t(`proficiency_${s.proficiency}` as "proficiency_basic")
                      : s.proficiency}
                  </span>
                )}
                <StatusBadge status={s.status} />
                <button
                  type="button"
                  data-testid={`skill-edit-${i}`}
                  aria-label={t("entryEditor.editEntryAria", { label })}
                  disabled={isDenied}
                  onClick={() => openEdit(i)}
                  className="text-xs font-medium text-primary hover:underline disabled:cursor-not-allowed disabled:opacity-40 disabled:no-underline"
                >
                  {t("edit")}
                </button>
                <button
                  type="button"
                  data-testid={`skill-remove-${i}`}
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
        data-testid="skills-add"
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
              dialog.index === null ? t("skillsEditor.dialogTitleAdd") : t("skillsEditor.dialogTitleEdit")
            }
            data-testid="skill-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null ? t("skillsEditor.dialogTitleAdd") : t("skillsEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label htmlFor="skill-name" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("skillsEditor.fieldName")}
                  </label>
                  <input
                    id="skill-name"
                    ref={firstFieldRef}
                    data-testid="skill-field-name"
                    value={dialog.draft.name ?? ""}
                    onChange={(e) => updateDraft({ name: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="skill-category" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("skillsEditor.fieldCategory")}
                  </label>
                  <select
                    id="skill-category"
                    data-testid="skill-field-category"
                    value={dialog.draft.category ?? "technical"}
                    onChange={(e) => updateDraft({ category: e.target.value as Skill["category"] })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  >
                    {CATEGORY_VALUES.map((c) => (
                      <option key={c} value={c}>
                        {t(`skillsEditor.category${c.charAt(0).toUpperCase()}${c.slice(1)}` as "skillsEditor.categoryTechnical")}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="skill-proficiency"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("skillsEditor.fieldProficiency")}
                  </label>
                  <select
                    id="skill-proficiency"
                    data-testid="skill-field-proficiency"
                    value={dialog.draft.proficiency ?? "intermediate"}
                    onChange={(e) => updateDraft({ proficiency: e.target.value as Skill["proficiency"] })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  >
                    {PROFICIENCY_VALUES.map((p) => (
                      <option key={p} value={p}>
                        {t(`proficiency_${p}` as "proficiency_basic")}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="skill-years-experience"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("skillsEditor.fieldYearsExperience")}
                  </label>
                  <input
                    id="skill-years-experience"
                    data-testid="skill-field-years-experience"
                    type="number"
                    min={0}
                    value={dialog.draft.years_experience ?? ""}
                    onChange={(e) =>
                      updateDraft({
                        years_experience: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                {staleNotice && (
                  <p
                    data-testid="skill-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="skill-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="skill-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="skill-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="skill-entry-save"
                  onClick={() => void handleSubmit()}
                  disabled={saving}
                  className="rounded-lg bg-primary px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50"
                >
                  {saving
                    ? t("saving")
                    : dialog.index === null
                      ? t("entryEditor.addAsConfirmed")
                      : tCommon("save")}
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
            data-testid="skill-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemoveIndex(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">{t("entryEditor.removeEntryBody")}</p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="skill-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="skill-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemoveIndex(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="skill-entry-remove-confirm"
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
