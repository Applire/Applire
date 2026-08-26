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

// US291 — structured editor for the Master Profile's certifications section,
// replacing the JSON textarea. Mirrors WorkExperienceEditor's dialog pattern.
// date_obtained/expiry_date go through PartialDateField (YYYY-MM/YYYY/null;
// a stored YYYY-MM-DD parses back fine, a legacy unparseable value is shown
// verbatim and preserved). `status` is a read-only badge (H2.1); a NEW
// certification is sent with an EXPLICIT `status: "confirmed"` (H2.2, PO
// ruling 2026-08-25).

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import {
  cloneEntry,
  makeEmptyCertification,
  nonEmptyText,
  type Certification,
  type ProfileSectionsResponse,
} from "@/lib/profile-entries";
import { saveProfileSection } from "@/lib/sectionSave";
import { PartialDateField } from "./PartialDateField";
import { StatusBadge } from "./StatusBadge";

interface DialogState {
  /** null = adding a new entry; otherwise the index being edited. */
  index: number | null;
  draft: Certification;
}

interface PendingRemove {
  /** Fallback identity for legacy id-less entries only — see F1. */
  index: number;
  /** Non-empty entry id when available; the removal is keyed on THIS, not the index. */
  id: string | null;
  /** Raw entry label, or null when nothing nameable was on the entry (H2 F1c). */
  label: string | null;
}

interface CertificationsEditorProps {
  entries: Certification[];
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

export function CertificationsEditor({
  entries,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: CertificationsEditorProps) {
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

  const dialogOpen = dialog !== null;
  const dialogIndex = dialog?.index ?? -1;
  useEffect(() => {
    if (dialogOpen) firstFieldRef.current?.focus();
  }, [dialogOpen, dialogIndex]);
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
    setDialog({ index: null, draft: makeEmptyCertification() });
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

  function updateDraft(patch: Partial<Certification>) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...patch } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;
    const { index } = dialog;
    const name = (dialog.draft.name ?? "").trim();
    // A year alone is coerced to 1 January by the backend `date` field — a
    // fabricated day nobody chose (adversarial finding 2026-08-26). Require the
    // month whenever a year is given; "unknown" (null) stays allowed.
    const yearOnly = /^\d{4}$/;
    if (yearOnly.test(dialog.draft.date_obtained ?? "") || yearOnly.test(dialog.draft.expiry_date ?? "")) {
      setValidationError(t("entryEditor.validationMonthRequired"));
      return;
    }
    if (!name) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const draft = {
      ...dialog.draft,
      name,
      issuing_organization: nonEmptyText(dialog.draft.issuing_organization)
        ? dialog.draft.issuing_organization!.trim()
        : null,
      credential_id: nonEmptyText(dialog.draft.credential_id) ? dialog.draft.credential_id!.trim() : null,
      credential_url: nonEmptyText(dialog.draft.credential_url) ? dialog.draft.credential_url!.trim() : null,
    };

    let nextEntries: Certification[];
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
      section: "certifications",
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
      section: "certifications",
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
          data-testid="certifications-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}
      {listStaleNotice && (
        <div
          data-testid="certifications-stale-notice"
          className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
        >
          {t("entryEditor.staleNotice")}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
      ) : (
        <ul className="space-y-2">
          {entries.map((c, i) => {
            const rawLabel = nonEmptyText(c.name) ? c.name : null;
            const label = rawLabel ?? t("notProvided");
            const when = c.date_obtained;
            return (
              <li
                key={nonEmptyText(c.id) ? c.id : i}
                className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 rounded-lg border border-outline-variant bg-white px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-medium text-neutral-dark">{label}</span>
                  {[c.issuing_organization, when].some(nonEmptyText) && (
                    <span className="text-xs text-gray-500">
                      {[c.issuing_organization, when].filter(nonEmptyText).join(" · ")}
                    </span>
                  )}
                  <StatusBadge status={c.status} />
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    data-testid={`certification-edit-${i}`}
                    aria-label={t("entryEditor.editEntryAria", { label })}
                    onClick={() => openEdit(i)}
                    className="text-xs font-medium text-primary hover:underline"
                  >
                    {t("edit")}
                  </button>
                  <button
                    type="button"
                    data-testid={`certification-remove-${i}`}
                    aria-label={t("entryEditor.removeEntryAria", { label })}
                    onClick={() => {
                      setRemoveError(null);
                      setPendingRemove({
                        index: i,
                        id: nonEmptyText(c.id) ? c.id : null,
                        label: rawLabel,
                      });
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
        data-testid="certifications-add"
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
              dialog.index === null
                ? t("certificationsEditor.dialogTitleAdd")
                : t("certificationsEditor.dialogTitleEdit")
            }
            data-testid="certification-entry-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">
                {dialog.index === null
                  ? t("certificationsEditor.dialogTitleAdd")
                  : t("certificationsEditor.dialogTitleEdit")}
              </h3>

              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="certification-name"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("certificationsEditor.fieldName")}
                  </label>
                  <input
                    id="certification-name"
                    ref={firstFieldRef}
                    data-testid="certification-field-name"
                    value={dialog.draft.name ?? ""}
                    onChange={(e) => updateDraft({ name: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="certification-issuer"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("certificationsEditor.fieldIssuingOrganization")}
                  </label>
                  <input
                    id="certification-issuer"
                    data-testid="certification-field-issuing-organization"
                    value={dialog.draft.issuing_organization ?? ""}
                    onChange={(e) => updateDraft({ issuing_organization: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <PartialDateField
                    id="certification-date-obtained"
                    label={t("certificationsEditor.fieldDateObtained")}
                    value={dialog.draft.date_obtained ?? null}
                    onChange={(v) => updateDraft({ date_obtained: v })}
                  />
                  <PartialDateField
                    id="certification-expiry-date"
                    label={t("certificationsEditor.fieldExpiryDate")}
                    value={dialog.draft.expiry_date ?? null}
                    onChange={(v) => updateDraft({ expiry_date: v })}
                  />
                </div>

                <div>
                  <label
                    htmlFor="certification-credential-id"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("certificationsEditor.fieldCredentialId")}
                  </label>
                  <input
                    id="certification-credential-id"
                    data-testid="certification-field-credential-id"
                    value={dialog.draft.credential_id ?? ""}
                    onChange={(e) => updateDraft({ credential_id: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                <div>
                  <label
                    htmlFor="certification-credential-url"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("certificationsEditor.fieldCredentialUrl")}
                  </label>
                  <input
                    id="certification-credential-url"
                    data-testid="certification-field-credential-url"
                    value={dialog.draft.credential_url ?? ""}
                    onChange={(e) => updateDraft({ credential_url: e.target.value })}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>

                {staleNotice && (
                  <p
                    data-testid="certification-entry-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="certification-entry-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="certification-entry-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="certification-entry-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="certification-entry-save"
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

      {pendingRemove !== null &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("entryEditor.removeEntryTitle")}
            data-testid="certification-entry-remove-dialog"
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
                <p className="mb-3 text-sm text-critical" data-testid="certification-entry-remove-error">
                  {removeError}
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="certification-entry-remove-cancel"
                  disabled={removeBusy}
                  onClick={() => setPendingRemove(null)}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="certification-entry-remove-confirm"
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
