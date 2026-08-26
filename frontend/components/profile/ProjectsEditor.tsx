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

// US292 — structured editor for the Master Profile's projects section,
// replacing the JSON textarea. Mirrors WorkExperienceEditor's dialog pattern
// (ExperienceBase-shaped: PartialDateField dates, tri-state is_current, three
// BulletListFields) with `name` as the natural key instead of company/role.
// `associated_experience` is a free-text label of a work/volunteer entry
// (e.g. "TechVision GmbH") — not a picker, the backend does not enforce a
// foreign key here (ADR-044). Projects carry no `status` badge.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  formatEntryPeriod,
  makeEmptyProjectEntry,
  nonEmptyText,
  trimStringList,
  type ProfileSectionsResponse,
  type ProjectEntry,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { PartialDateField } from "./PartialDateField";
import { BulletListField } from "./BulletListField";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: ProjectEntry;
}

interface PendingRemove {
  /** Fallback identity for legacy id-less entries only — see F1. */
  index: number;
  /** Non-empty entry id when available; the removal is keyed on THIS, not the index. */
  id: string | null;
  /** Raw entry label, or null when nothing nameable was on the entry (H2 F1c). */
  label: string | null;
}

interface ProjectsEditorProps {
  entries: ProjectEntry[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

export function ProjectsEditor({
  entries,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: ProjectsEditorProps) {
  const t = useTranslations("profile");
  const tCommon = useTranslations("common");

  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [staleNotice, setStaleNotice] = useState(false);
  const [mismatchNotice, setMismatchNotice] = useState(false);
  const [listStaleNotice, setListStaleNotice] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<PendingRemove | null>(null);
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
  // Double-submit guard: `saving` is React state and lags a second click by a
  // render; a ref closes the gap.
  const inFlight = useRef(false);

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

  const removeDialogOpen = pendingRemove !== null;
  useEffect(() => {
    if (!removeDialogOpen) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setPendingRemove(null);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [removeDialogOpen]);

  function openAdd() {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    setDialog({ index: null, draft: makeEmptyProjectEntry() });
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

  function updateDraft(patch: Partial<ProjectEntry>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const name = (dialog.draft.name ?? "").trim();
    if (!name) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const draft: ProjectEntry = {
      ...dialog.draft,
      name,
      role: (dialog.draft.role ?? "").trim(),
      location: nonEmptyText(dialog.draft.location) ? dialog.draft.location!.trim() : null,
      description: nonEmptyText(dialog.draft.description) ? dialog.draft.description!.trim() : null,
      url: nonEmptyText(dialog.draft.url) ? dialog.draft.url!.trim() : null,
      associated_experience: nonEmptyText(dialog.draft.associated_experience)
        ? dialog.draft.associated_experience!.trim()
        : null,
      responsibilities: trimStringList(dialog.draft.responsibilities),
      achievements: trimStringList(dialog.draft.achievements),
      technologies: trimStringList(dialog.draft.technologies),
    };

    let nextEntries: ProjectEntry[];
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
      section: "projects",
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
    if (!pendingRemove) return;
    // F1 — key the removal on the entry's id whenever it has one; the index
    // is only a fallback for legacy id-less entries (see WorkExperienceEditor).
    const nextEntries =
      pendingRemove.id !== null
        ? entries.filter((e) => e.id !== pendingRemove.id)
        : entries.filter((_, i) => i !== pendingRemove.index);
    setRemoveBusy(true);
    setRemoveError(null);
    const result = await saveProfileSection<ProfileSectionsResponse>({
      apiBase,
      section: "projects",
      entries: nextEntries,
      basisUpdatedAt: profileUpdatedAt,
    });
    setRemoveBusy(false);

    if (result.status === "ok") {
      onProfileUpdated(result.profile);
      setPendingRemove(null);
      return;
    }
    if (result.status === "stale") {
      // F1 — close the confirm dialog rather than retrying on a stale index.
      onProfileUpdated(result.current);
      setPendingRemove(null);
      setListStaleNotice(true);
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
          data-testid="projects-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}
      {listStaleNotice && (
        <div
          data-testid="projects-stale-notice"
          className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
        >
          {t("entryEditor.staleNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <div className="space-y-4">
          {entries.map((e, i) => {
            const rawLabel = nonEmptyText(e.name) ? e.name : null;
            const label = rawLabel ?? t("notProvided");
            const period = formatEntryPeriod(e.start_date, e.end_date, t("present"), e.is_current);
            return (
              <div key={nonEmptyText(e.id) ? e.id : i} className="border-l-2 border-teal/40 pl-3">
                <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <p className="text-sm font-semibold text-neutral-dark">
                    {[label, nonEmptyText(e.role) ? e.role : null].filter(Boolean).join(" · ")}
                  </p>
                  <div className="flex items-center gap-2">
                    {period && <span className="text-xs text-gray-500">{period}</span>}
                    <button
                      type="button"
                      data-testid={`project-edit-${i}`}
                      aria-label={t("entryEditor.editEntryAria", { label })}
                      onClick={() => openEdit(i)}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      data-testid={`project-remove-${i}`}
                      aria-label={t("entryEditor.removeEntryAria", { label })}
                      onClick={() => {
                        setRemoveError(null);
                        setPendingRemove({
                          index: i,
                          id: nonEmptyText(e.id) ? e.id : null,
                          label: rawLabel,
                        });
                      }}
                      className="text-xs font-medium text-critical hover:underline"
                    >
                      {tCommon("delete")}
                    </button>
                  </div>
                </div>
                {nonEmptyText(e.description) && (
                  <p className="mt-1 text-sm text-gray-700">{e.description}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        data-testid="projects-add"
        onClick={openAdd}
        className="mt-3 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("projectsEditor.addButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog.index === null ? t("projectsEditor.dialogTitleAdd") : t("projectsEditor.dialogTitleEdit")
            }
            data-testid="project-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null ? t("projectsEditor.dialogTitleAdd") : t("projectsEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label htmlFor="project-name" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("projectsEditor.fieldName")}
                  </label>
                  <input
                    id="project-name"
                    ref={firstFieldRef}
                    data-testid="project-field-name"
                    value={dialog.draft.name ?? ""}
                    onChange={(e) => updateDraft({ name: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="project-role" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("projectsEditor.fieldRole")}
                  </label>
                  <input
                    id="project-role"
                    data-testid="project-field-role"
                    value={dialog.draft.role ?? ""}
                    onChange={(e) => updateDraft({ role: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="project-associated-experience"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("projectsEditor.fieldAssociatedExperience")}
                  </label>
                  <input
                    id="project-associated-experience"
                    data-testid="project-field-associated-experience"
                    value={dialog.draft.associated_experience ?? ""}
                    onChange={(e) => updateDraft({ associated_experience: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="project-location"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("projectsEditor.fieldLocation")}
                  </label>
                  <input
                    id="project-location"
                    data-testid="project-field-location"
                    value={dialog.draft.location ?? ""}
                    onChange={(e) => updateDraft({ location: e.target.value || null })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <PartialDateField
                    id="project-start-date"
                    label={t("workEditor.fieldStartDate")}
                    value={dialog.draft.start_date ?? null}
                    onChange={(v) => updateDraft({ start_date: v })}
                  />
                  <PartialDateField
                    id="project-end-date"
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
                        name="project-is-current"
                        data-testid="project-is-current-current"
                        checked={dialog.draft.is_current === true}
                        onChange={() => updateDraft({ is_current: true, end_date: null })}
                      />
                      {t("entryEditor.currentStatusCurrent")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="project-is-current"
                        data-testid="project-is-current-ended"
                        checked={dialog.draft.is_current === false}
                        onChange={() => updateDraft({ is_current: false })}
                      />
                      {t("entryEditor.currentStatusEnded")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm text-on-surface">
                      <input
                        type="radio"
                        name="project-is-current"
                        data-testid="project-is-current-unknown"
                        checked={dialog.draft.is_current === null || dialog.draft.is_current === undefined}
                        onChange={() => updateDraft({ is_current: null })}
                      />
                      {t("entryEditor.currentStatusUnknown")}
                    </label>
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="project-description"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("projectsEditor.fieldDescription")}
                  </label>
                  <textarea
                    id="project-description"
                    data-testid="project-field-description"
                    value={dialog.draft.description ?? ""}
                    onChange={(e) => updateDraft({ description: e.target.value })}
                    rows={3}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="project-url" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("projectsEditor.fieldUrl")}
                  </label>
                  <input
                    id="project-url"
                    data-testid="project-field-url"
                    value={dialog.draft.url ?? ""}
                    onChange={(e) => updateDraft({ url: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <BulletListField
                  id="project-responsibilities"
                  label={t("workEditor.fieldResponsibilities")}
                  items={dialog.draft.responsibilities ?? []}
                  onChange={(items) => updateDraft({ responsibilities: items })}
                  addButtonLabel={t("workEditor.addResponsibility")}
                  itemAriaLabel={(i) => t("workEditor.responsibilityAria", { index: i + 1 })}
                />
                <BulletListField
                  id="project-achievements"
                  label={t("workEditor.fieldAchievements")}
                  items={dialog.draft.achievements ?? []}
                  onChange={(items) => updateDraft({ achievements: items })}
                  addButtonLabel={t("workEditor.addAchievement")}
                  itemAriaLabel={(i) => t("workEditor.achievementAria", { index: i + 1 })}
                />
                <BulletListField
                  id="project-technologies"
                  label={t("workEditor.fieldTechnologies")}
                  items={dialog.draft.technologies ?? []}
                  onChange={(items) => updateDraft({ technologies: items })}
                  addButtonLabel={t("workEditor.addTechnology")}
                  itemAriaLabel={(i) => t("workEditor.technologyAria", { index: i + 1 })}
                  allowReorder={false}
                />

                {staleNotice && (
                  <p
                    data-testid="project-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="project-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="project-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="project-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="project-entry-save"
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

      {pendingRemove !== null &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("entryEditor.removeEntryTitle")}
            data-testid="project-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemove(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">
                {pendingRemove.label
                  ? t("entryEditor.removeEntryBodyNamed", { label: pendingRemove.label })
                  : t("entryEditor.removeEntryBody")}
              </p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="project-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="project-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemove(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="project-entry-remove-confirm"
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
