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

// US292 — structured editor for the Master Profile's publications section,
// replacing the JSON textarea. `Publication` is its own BaseModel (NOT an
// ExperienceBase subclass) — no role/location/dates-span/is_current/bullet
// lists here, just title, type, co_authors, venue, a single published_date,
// and the identifier fields. `published_date` is a true backend `date` field
// (like Certification's dates): a year alone would be coerced to 1 January,
// so a year-only value is refused exactly like CertificationsEditor does.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  makeEmptyPublication,
  nonEmptyText,
  trimStringList,
  type ProfileSectionsResponse,
  type Publication,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { PartialDateField } from "./PartialDateField";
import { BulletListField } from "./BulletListField";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: Publication;
}

interface PublicationsEditorProps {
  entries: Publication[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

const YEAR_ONLY = /^\d{4}$/;

export function PublicationsEditor({
  entries,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: PublicationsEditorProps) {
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
    setDialog({ index: null, draft: makeEmptyPublication() });
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

  function updateDraft(patch: Partial<Publication>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const title = (dialog.draft.title ?? "").trim();
    // A year alone is coerced to 1 January by the backend `date` field — a
    // fabricated day nobody chose, exactly like CertificationsEditor's guard.
    if (YEAR_ONLY.test(dialog.draft.published_date ?? "")) {
      setValidationError(t("entryEditor.validationMonthRequired"));
      return;
    }
    if (!title) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const draft: Publication = {
      ...dialog.draft,
      title,
      co_authors: trimStringList(dialog.draft.co_authors),
      venue: nonEmptyText(dialog.draft.venue) ? dialog.draft.venue!.trim() : null,
      doi: nonEmptyText(dialog.draft.doi) ? dialog.draft.doi!.trim() : null,
      url: nonEmptyText(dialog.draft.url) ? dialog.draft.url!.trim() : null,
      patent_number: nonEmptyText(dialog.draft.patent_number) ? dialog.draft.patent_number!.trim() : null,
    };

    let nextEntries: Publication[];
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
      section: "publications",
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
      section: "publications",
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
          data-testid="publications-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <ul className="space-y-2">
          {entries.map((p, i) => {
            const label = nonEmptyText(p.title) ? p.title : t("notProvided");
            return (
              <li
                key={p.id ?? i}
                className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 rounded-lg border border-outline-variant bg-white px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-medium text-neutral-dark">{label}</span>
                  {p.type === "patent" && (
                    <span
                      data-testid={`publication-type-badge-${i}`}
                      className="inline-flex items-center rounded-full bg-surface-container px-2 py-0.5 text-[11px] font-medium text-on-surface-variant"
                    >
                      {t("publicationsEditor.typePatent")}
                    </span>
                  )}
                  {[p.venue, p.published_date].some(nonEmptyText) && (
                    <span className="text-xs text-gray-500">
                      {[p.venue, p.published_date].filter(nonEmptyText).join(" · ")}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    data-testid={`publication-edit-${i}`}
                    aria-label={t("entryEditor.editEntryAria", { label })}
                    onClick={() => openEdit(i)}
                    className="text-xs font-medium text-primary hover:underline"
                  >
                    {t("edit")}
                  </button>
                  <button
                    type="button"
                    data-testid={`publication-remove-${i}`}
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
              </li>
            );
          })}
        </ul>
      )}

      <button
        type="button"
        data-testid="publications-add"
        onClick={openAdd}
        className="mt-3 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("publicationsEditor.addButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog.index === null
                ? t("publicationsEditor.dialogTitleAdd")
                : t("publicationsEditor.dialogTitleEdit")
            }
            data-testid="publication-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null
                  ? t("publicationsEditor.dialogTitleAdd")
                  : t("publicationsEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="publication-title"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("publicationsEditor.fieldTitle")}
                  </label>
                  <input
                    id="publication-title"
                    ref={firstFieldRef}
                    data-testid="publication-field-title"
                    value={dialog.draft.title ?? ""}
                    onChange={(e) => updateDraft({ title: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="publication-type"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("publicationsEditor.fieldType")}
                  </label>
                  <select
                    id="publication-type"
                    data-testid="publication-field-type"
                    value={dialog.draft.type ?? "publication"}
                    onChange={(e) => updateDraft({ type: e.target.value as Publication["type"] })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  >
                    <option value="publication">{t("publicationsEditor.typePublication")}</option>
                    <option value="patent">{t("publicationsEditor.typePatent")}</option>
                  </select>
                </div>

                <BulletListField
                  id="publication-co-authors"
                  label={t("publicationsEditor.fieldCoAuthors")}
                  items={dialog.draft.co_authors ?? []}
                  onChange={(items) => updateDraft({ co_authors: items })}
                  addButtonLabel={t("publicationsEditor.addCoAuthor")}
                  itemAriaLabel={(i) => t("publicationsEditor.coAuthorAria", { index: i + 1 })}
                />

                <div>
                  <label
                    htmlFor="publication-venue"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("publicationsEditor.fieldVenue")}
                  </label>
                  <input
                    id="publication-venue"
                    data-testid="publication-field-venue"
                    value={dialog.draft.venue ?? ""}
                    onChange={(e) => updateDraft({ venue: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <PartialDateField
                  id="publication-published-date"
                  label={t("publicationsEditor.fieldPublishedDate")}
                  value={dialog.draft.published_date ?? null}
                  onChange={(v) => updateDraft({ published_date: v })}
                />

                <div>
                  <label htmlFor="publication-doi" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("publicationsEditor.fieldDoi")}
                  </label>
                  <input
                    id="publication-doi"
                    data-testid="publication-field-doi"
                    value={dialog.draft.doi ?? ""}
                    onChange={(e) => updateDraft({ doi: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label htmlFor="publication-url" className="mb-1 block text-xs font-medium text-on-surface-variant">
                    {t("publicationsEditor.fieldUrl")}
                  </label>
                  <input
                    id="publication-url"
                    data-testid="publication-field-url"
                    value={dialog.draft.url ?? ""}
                    onChange={(e) => updateDraft({ url: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="publication-patent-number"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("publicationsEditor.fieldPatentNumber")}
                  </label>
                  <input
                    id="publication-patent-number"
                    data-testid="publication-field-patent-number"
                    value={dialog.draft.patent_number ?? ""}
                    onChange={(e) => updateDraft({ patent_number: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                {staleNotice && (
                  <p
                    data-testid="publication-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="publication-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="publication-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="publication-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="publication-entry-save"
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
            data-testid="publication-entry-remove-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") setPendingRemoveIndex(null);
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">{t("entryEditor.removeEntryTitle")}</h3>
              <p className="mb-4 text-sm text-on-surface-variant">{t("entryEditor.removeEntryBody")}</p>
              {removeError && (
                <p className="mb-3 text-sm text-critical" data-testid="publication-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="publication-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemoveIndex(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="publication-entry-remove-confirm"
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
