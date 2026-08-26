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

// US292 — structured editor for the Master Profile's volunteer_activities
// section, replacing the JSON textarea. Mirrors WorkExperienceEditor's
// dialog pattern (ExperienceBase-shaped: PartialDateField dates, tri-state
// is_current, three BulletListFields). The natural key is (organization,
// role) — both required, unlike WorkEntry where company+role are also both
// required but VolunteerActivity additionally exposes `cause`.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  formatEntryPeriod,
  makeEmptyVolunteerActivity,
  nonEmptyText,
  trimStringList,
  type ProfileSectionsResponse,
  type VolunteerActivity,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { PartialDateField } from "./PartialDateField";
import { BulletListField } from "./BulletListField";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: VolunteerActivity;
}

interface VolunteerEditorProps {
  entries: VolunteerActivity[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

function entryLabel(entry: VolunteerActivity): string {
  return [entry.role, entry.organization].filter((s) => nonEmptyText(s)).join(" @ ");
}

export function VolunteerEditor({
  entries,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: VolunteerEditorProps) {
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
  // entry index, never on the `dialog` object itself (adversarial finding
  // 2026-08-25, blocker — see WorkExperienceEditor).
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
    setDialog({ index: null, draft: makeEmptyVolunteerActivity() });
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

  function updateDraft(patch: Partial<VolunteerActivity>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const organization = (dialog.draft.organization ?? "").trim();
    const role = (dialog.draft.role ?? "").trim();
    if (!organization || !role) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const draft: VolunteerActivity = {
      ...dialog.draft,
      organization,
      role,
      cause: nonEmptyText(dialog.draft.cause) ? dialog.draft.cause!.trim() : null,
      location: nonEmptyText(dialog.draft.location) ? dialog.draft.location!.trim() : null,
      description: nonEmptyText(dialog.draft.description) ? dialog.draft.description!.trim() : null,
      responsibilities: trimStringList(dialog.draft.responsibilities),
      achievements: trimStringList(dialog.draft.achievements),
      technologies: trimStringList(dialog.draft.technologies),
    };

    let nextEntries: VolunteerActivity[];
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
      section: "volunteer_activities",
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
      section: "volunteer_activities",
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
          data-testid="volunteer-mismatch-notice"
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
            const period = formatEntryPeriod(e.start_date, e.end_date, t("present"), e.is_current);
            const label = entryLabel(e) || t("notProvided");
            return (
              <div key={e.id ?? i} className="border-l-2 border-teal/40 pl-3">
                <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <p className="text-sm font-semibold text-neutral-dark">
                    {e.role || e.organization || t("notProvided")}
                  </p>
                  <div className="flex items-center gap-2">
                    {period && <span className="text-xs text-gray-500">{period}</span>}
                    <button
                      type="button"
                      data-testid={`volunteer-edit-${i}`}
                      aria-label={t("entryEditor.editEntryAria", { label })}
                      onClick={() => openEdit(i)}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      data-testid={`volunteer-remove-${i}`}
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
                {nonEmptyText(e.organization) && e.role !== e.organization && (
                  <p className="text-xs text-gray-600">{e.organization}</p>
                )}
                {nonEmptyText(e.cause) && <p className="text-xs text-gray-500">{e.cause}</p>}
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        data-testid="volunteer-add"
        onClick={openAdd}
        className="mt-3 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("volunteerEditor.addButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog.index === null ? t("volunteerEditor.dialogTitleAdd") : t("volunteerEditor.dialogTitleEdit")
            }
            data-testid="volunteer-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null
                  ? t("volunteerEditor.dialogTitleAdd")
                  : t("volunteerEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="volunteer-organization"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("volunteerEditor.fieldOrganization")}
                  </label>
                  <input
                    id="volunteer-organization"
                    ref={firstFieldRef}
                    data-testid="volunteer-field-organization"
                    value={dialog.draft.organization ?? ""}
                    onChange={(e) => updateDraft({ organization: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="volunteer-role"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("volunteerEditor.fieldRole")}
                  </label>
                  <input
                    id="volunteer-role"
                    data-testid="volunteer-field-role"
                    value={dialog.draft.role ?? ""}
                    onChange={(e) => updateDraft({ role: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="volunteer-cause"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("volunteerEditor.fieldCause")}
                  </label>
                  <input
                    id="volunteer-cause"
                    data-testid="volunteer-field-cause"
                    value={dialog.draft.cause ?? ""}
                    onChange={(e) => updateDraft({ cause: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="volunteer-location"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("volunteerEditor.fieldLocation")}
                  </label>
                  <input
                    id="volunteer-location"
                    data-testid="volunteer-field-location"
                    value={dialog.draft.location ?? ""}
                    onChange={(e) => updateDraft({ location: e.target.value || null })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <PartialDateField
                    id="volunteer-start-date"
                    label={t("workEditor.fieldStartDate")}
                    value={dialog.draft.start_date ?? null}
                    onChange={(v) => updateDraft({ start_date: v })}
                  />
                  <PartialDateField
                    id="volunteer-end-date"
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
                        name="volunteer-is-current"
                        data-testid="volunteer-is-current-current"
                        checked={dialog.draft.is_current === true}
                        onChange={() => updateDraft({ is_current: true, end_date: null })}
                      />
                      {t("entryEditor.currentStatusCurrent")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="volunteer-is-current"
                        data-testid="volunteer-is-current-ended"
                        checked={dialog.draft.is_current === false}
                        onChange={() => updateDraft({ is_current: false })}
                      />
                      {t("entryEditor.currentStatusEnded")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="volunteer-is-current"
                        data-testid="volunteer-is-current-unknown"
                        checked={dialog.draft.is_current === null || dialog.draft.is_current === undefined}
                        onChange={() => updateDraft({ is_current: null })}
                      />
                      {t("entryEditor.currentStatusUnknown")}
                    </label>
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="volunteer-description"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("volunteerEditor.fieldDescription")}
                  </label>
                  <textarea
                    id="volunteer-description"
                    data-testid="volunteer-field-description"
                    value={dialog.draft.description ?? ""}
                    onChange={(e) => updateDraft({ description: e.target.value })}
                    rows={3}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <BulletListField
                  id="volunteer-responsibilities"
                  label={t("workEditor.fieldResponsibilities")}
                  items={dialog.draft.responsibilities ?? []}
                  onChange={(items) => updateDraft({ responsibilities: items })}
                  addButtonLabel={t("workEditor.addResponsibility")}
                  itemAriaLabel={(i) => t("workEditor.responsibilityAria", { index: i + 1 })}
                />
                <BulletListField
                  id="volunteer-achievements"
                  label={t("workEditor.fieldAchievements")}
                  items={dialog.draft.achievements ?? []}
                  onChange={(items) => updateDraft({ achievements: items })}
                  addButtonLabel={t("workEditor.addAchievement")}
                  itemAriaLabel={(i) => t("workEditor.achievementAria", { index: i + 1 })}
                />
                <BulletListField
                  id="volunteer-technologies"
                  label={t("workEditor.fieldTechnologies")}
                  items={dialog.draft.technologies ?? []}
                  onChange={(items) => updateDraft({ technologies: items })}
                  addButtonLabel={t("workEditor.addTechnology")}
                  itemAriaLabel={(i) => t("workEditor.technologyAria", { index: i + 1 })}
                  allowReorder={false}
                />

                {staleNotice && (
                  <p
                    data-testid="volunteer-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="volunteer-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="volunteer-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="volunteer-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="volunteer-entry-save"
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
            data-testid="volunteer-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemoveIndex(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">{t("entryEditor.removeEntryBody")}</p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="volunteer-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="volunteer-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemoveIndex(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="volunteer-entry-remove-confirm"
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
