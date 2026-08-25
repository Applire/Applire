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

// US290 — structured editor for the Master Profile's work_experience section,
// replacing the JSON textarea. Per-entry "Bearbeiten" opens an edit dialog;
// "Eintrag hinzufügen" opens the same dialog for a new entry. Only company,
// role, location, dates, the tri-state current-position marker, and the
// three bullet lists get a form control here — industry_context, team_size,
// budget_managed, role_aliases, expected_fields and role_fact_projections
// are preserved verbatim (spread-through) but stay editable via the existing
// enrichment conversation, not this door (see US290 report deviation note).

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  formatEntryPeriod,
  makeEmptyWorkEntry,
  nonEmptyText,
  type ProfileSectionsResponse,
  type WorkEntry,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { PartialDateField } from "./PartialDateField";
import { BulletListField } from "./BulletListField";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: WorkEntry;
}

interface WorkExperienceEditorProps {
  entries: WorkEntry[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

function entryLabel(entry: WorkEntry): string {
  return [entry.role, entry.company].filter((s) => nonEmptyText(s)).join(" @ ");
}

export function WorkExperienceEditor({
  entries,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: WorkExperienceEditorProps) {
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

  useEffect(() => {
    if (dialog) firstFieldRef.current?.focus();
  }, [dialog]);

  function openAdd() {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    setDialog({ index: null, draft: makeEmptyWorkEntry() });
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

  function updateDraft(patch: Partial<WorkEntry>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    const { index, draft } = dialog;
    const company = (draft.company ?? "").trim();
    const role = (draft.role ?? "").trim();
    if (!company || !role) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }

    let nextEntries: WorkEntry[];
    let savedEntryId: string | undefined;
    if (index === null) {
      const { id: _unused, ...withoutId } = draft;
      void _unused;
      nextEntries = [...entries, withoutId];
    } else {
      nextEntries = entries.map((e, i) => (i === index ? draft : e));
      savedEntryId = draft.id;
    }

    setSaving(true);
    setDialogError(null);
    setStaleNotice(false);
    const result = await saveProfileSection<ProfileSectionsResponse>({
      apiBase,
      section: "work_experience",
      entries: nextEntries,
      basisUpdatedAt: profileUpdatedAt,
      savedEntryId,
    });
    setSaving(false);

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
      section: "work_experience",
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
          data-testid="work-experience-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <div className="space-y-4">
          {entries.map((e, i) => {
            const role = e.role || "";
            const period = formatEntryPeriod(e.start_date, e.end_date, t("present"), e.is_current);
            const bullets = [...(e.achievements ?? []), ...(e.responsibilities ?? [])].filter(nonEmptyText);
            const label = entryLabel(e) || t("notProvided");
            return (
              <div key={e.id ?? i} className="border-l-2 border-teal/40 pl-3">
                <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <p className="text-sm font-semibold text-neutral-dark">
                    {role || e.company || t("notProvided")}
                  </p>
                  <div className="flex items-center gap-2">
                    {period && <span className="text-xs text-gray-500">{period}</span>}
                    <button
                      type="button"
                      data-testid={`work-entry-edit-${i}`}
                      aria-label={t("entryEditor.editEntryAria", { label })}
                      onClick={() => openEdit(i)}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      data-testid={`work-entry-remove-${i}`}
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
                {nonEmptyText(e.company) && role !== e.company && (
                  <p className="text-xs text-gray-600">
                    {[e.company, nonEmptyText(e.location) ? e.location : null].filter(Boolean).join(" · ")}
                  </p>
                )}
                {bullets.length > 0 && (
                  <ul className="mt-1.5 list-disc pl-4 space-y-0.5 text-sm text-gray-700">
                    {bullets.map((b, bi) => (
                      <li key={bi}>{b}</li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        data-testid="work-experience-add"
        onClick={openAdd}
        className="mt-3 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("workEditor.addButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog.index === null ? t("workEditor.dialogTitleAdd") : t("workEditor.dialogTitleEdit")
            }
            data-testid="work-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null ? t("workEditor.dialogTitleAdd") : t("workEditor.dialogTitleEdit")}
              </h3>

              {staleNotice && (
                <p
                  data-testid="work-entry-stale-notice"
                  className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                >
                  {t("entryEditor.staleNotice")}
                </p>
              )}

              <div className="space-y-3">
                <div>
                  <label htmlFor="work-company" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("workEditor.fieldCompany")}
                  </label>
                  <input
                    id="work-company"
                    ref={firstFieldRef}
                    data-testid="work-field-company"
                    value={dialog.draft.company ?? ""}
                    onChange={(e) => updateDraft({ company: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="work-role" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("workEditor.fieldRole")}
                  </label>
                  <input
                    id="work-role"
                    data-testid="work-field-role"
                    value={dialog.draft.role ?? ""}
                    onChange={(e) => updateDraft({ role: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="work-location" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("workEditor.fieldLocation")}
                  </label>
                  <input
                    id="work-location"
                    data-testid="work-field-location"
                    value={dialog.draft.location ?? ""}
                    onChange={(e) => updateDraft({ location: e.target.value || null })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <PartialDateField
                    id="work-start-date"
                    label={t("workEditor.fieldStartDate")}
                    value={dialog.draft.start_date ?? null}
                    onChange={(v) => updateDraft({ start_date: v })}
                  />
                  <PartialDateField
                    id="work-end-date"
                    label={t("workEditor.fieldEndDate")}
                    value={dialog.draft.end_date ?? null}
                    onChange={(v) => updateDraft({ end_date: v })}
                    disabled={dialog.draft.is_current === true}
                  />
                </div>

                <div>
                  <p className="mb-1 text-xs font-medium text-on-surface-variant">
                    {t("entryEditor.currentStatusLabel")}
                  </p>
                  <div role="radiogroup" aria-label={t("entryEditor.currentStatusLabel")} className="flex gap-4">
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="work-is-current"
                        data-testid="work-is-current-current"
                        checked={dialog.draft.is_current === true}
                        onChange={() => updateDraft({ is_current: true, end_date: null })}
                      />
                      {t("entryEditor.currentStatusCurrent")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="work-is-current"
                        data-testid="work-is-current-ended"
                        checked={dialog.draft.is_current === false}
                        onChange={() => updateDraft({ is_current: false })}
                      />
                      {t("entryEditor.currentStatusEnded")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="work-is-current"
                        data-testid="work-is-current-unknown"
                        checked={dialog.draft.is_current === null || dialog.draft.is_current === undefined}
                        onChange={() => updateDraft({ is_current: null })}
                      />
                      {t("entryEditor.currentStatusUnknown")}
                    </label>
                  </div>
                </div>

                <BulletListField
                  id="work-responsibilities"
                  label={t("workEditor.fieldResponsibilities")}
                  items={dialog.draft.responsibilities ?? []}
                  onChange={(items) => updateDraft({ responsibilities: items })}
                  addButtonLabel={t("workEditor.addResponsibility")}
                  itemAriaLabel={(i) => t("workEditor.responsibilityAria", { index: i + 1 })}
                />
                <BulletListField
                  id="work-achievements"
                  label={t("workEditor.fieldAchievements")}
                  items={dialog.draft.achievements ?? []}
                  onChange={(items) => updateDraft({ achievements: items })}
                  addButtonLabel={t("workEditor.addAchievement")}
                  itemAriaLabel={(i) => t("workEditor.achievementAria", { index: i + 1 })}
                />
                <BulletListField
                  id="work-technologies"
                  label={t("workEditor.fieldTechnologies")}
                  items={dialog.draft.technologies ?? []}
                  onChange={(items) => updateDraft({ technologies: items })}
                  addButtonLabel={t("workEditor.addTechnology")}
                  itemAriaLabel={(i) => t("workEditor.technologyAria", { index: i + 1 })}
                  allowReorder={false}
                />

                {validationError && (
                  <p className="text-sm text-critical" data-testid="work-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="work-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="work-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="work-entry-save"
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
            data-testid="work-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemoveIndex(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">{t("entryEditor.removeEntryBody")}</p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="work-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="work-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemoveIndex(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="work-entry-remove-confirm"
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
