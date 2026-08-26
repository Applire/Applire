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
// `personal_info` OBJECT section. Like SummaryEditor, this renders only the
// edit affordance + dialog; the read-only display stays ProfileSectionCard's
// `ProfileSectionBody` (extended separately with the new fields).
//
// `photo_url` is owned by PhotoManager and is NEVER read from `value` nor
// written into the patch — it is simply absent from FIELD_KEYS below, so it
// cannot round-trip through this editor even if present on the prop.
// Likewise, any OTHER unknown key on `value` (a stray hand-edited field) is
// never copied into the draft and therefore never echoed back in a patch —
// a merge-patch must not send back what it did not edit.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { saveProfileObjectSection } from "@/lib/sectionSave";
import type { PersonalInfo, ProfileSectionsResponse } from "@/lib/profile-entries";

const FIELD_KEYS = [
  "name",
  "email",
  "phone",
  "location",
  "address",
  "nationality",
  "date_of_birth",
  "linkedin_url",
  "xing_url",
  "website_url",
] as const;

type FieldKey = (typeof FIELD_KEYS)[number];
type PersonalInfoDraft = Record<FieldKey, string>;
type PersonalInfoBaseline = Record<FieldKey, string | null>;

// Client-side pre-check only (mirrors the backend `date` field's accepted
// input shapes): "DD.MM.YYYY" / "D.M.YYYY" or ISO "YYYY-MM-DD".
const DATE_OF_BIRTH_PATTERN = /^(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})$/;
const GERMAN_DATE = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/;

/**
 * Send the date in the shape the backend STORES (ISO). The backend accepts
 * "DD.MM.YYYY" too, but it normalises on save — and the H0.4 mismatch
 * detector compares what was sent with what came back, so a value sent as
 * typed ("01.02.1990") would read as "not saved" against the stored
 * "1990-02-01" (integrator finding, real-browser pass 2026-08-26).
 */
function toIsoDate(value: string): string {
  const m = GERMAN_DATE.exec(value);
  if (!m) return value;
  return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
}

interface DialogState {
  draft: PersonalInfoDraft;
  baseline: PersonalInfoBaseline;
}

interface PersonalInfoEditorProps {
  value: PersonalInfo | null | undefined;
  apiBase: string;
  /** `updated_at` from the last GET this edit is based on (H1.6). */
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

function computeBaseline(value: PersonalInfo | null | undefined): PersonalInfoBaseline {
  const v = value ?? {};
  const baseline = {} as PersonalInfoBaseline;
  for (const key of FIELD_KEYS) {
    const raw = v[key];
    baseline[key] = typeof raw === "string" ? raw : null;
  }
  return baseline;
}

function draftFromBaseline(baseline: PersonalInfoBaseline): PersonalInfoDraft {
  const draft = {} as PersonalInfoDraft;
  for (const key of FIELD_KEYS) {
    draft[key] = baseline[key] ?? "";
  }
  return draft;
}

/** Trimmed-text comparison; a null/undefined baseline and an empty draft both mean "no change". */
function fieldChanged(draftText: string, baselineText: string | null): boolean {
  const trimmed = draftText.trim();
  if (baselineText === null || baselineText === undefined) return trimmed !== "";
  return trimmed !== baselineText.trim();
}

export function PersonalInfoEditor({
  value,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: PersonalInfoEditorProps) {
  const t = useTranslations("profile");
  const tCommon = useTranslations("common");

  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [staleNotice, setStaleNotice] = useState(false);
  const [mismatchNotice, setMismatchNotice] = useState(false);

  const nameFieldRef = useRef<HTMLInputElement | null>(null);
  const inFlight = useRef(false);

  const dialogOpen = dialog !== null;
  useEffect(() => {
    if (dialogOpen) nameFieldRef.current?.focus();
  }, [dialogOpen]);

  function openDialog() {
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    const baseline = computeBaseline(value);
    setDialog({ draft: draftFromBaseline(baseline), baseline });
  }

  function closeDialog() {
    setDialog(null);
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
  }

  function updateDraft(key: FieldKey, text: string) {
    setDialog((prev) => (prev ? { ...prev, draft: { ...prev.draft, [key]: text } } : prev));
  }

  async function handleSubmit() {
    if (!dialog) return;
    if (inFlight.current) return;

    if (!dialog.draft.name.trim()) {
      setValidationError(t("entryEditor.validationRequired"));
      return;
    }
    const dob = dialog.draft.date_of_birth.trim();
    if (dob && !DATE_OF_BIRTH_PATTERN.test(dob)) {
      setValidationError(t("personalInfoEditor.validationDateOfBirth"));
      return;
    }

    const patch: Record<string, unknown> = {};
    for (const key of FIELD_KEYS) {
      if (fieldChanged(dialog.draft[key], dialog.baseline[key])) {
        const trimmed = dialog.draft[key].trim();
        const normalised = key === "date_of_birth" ? toIsoDate(trimmed) : trimmed;
        patch[key] = normalised === "" ? null : normalised;
      }
    }

    if (Object.keys(patch).length === 0) {
      closeDialog();
      return;
    }

    inFlight.current = true;
    setSaving(true);
    setValidationError(null);
    setDialogError(null);
    setStaleNotice(false);
    const result = await saveProfileObjectSection<ProfileSectionsResponse>({
      apiBase,
      section: "personal_info",
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

  function textField(key: FieldKey, testid: string, label: string, htmlType: "text" | "email" = "text") {
    return (
      <div>
        <label htmlFor={`personal-info-${testid}`} className="mb-1 block text-xs font-medium text-on-surface-variant">
          {label}
        </label>
        <input
          id={`personal-info-${testid}`}
          ref={key === "name" ? nameFieldRef : undefined}
          data-testid={`personal-info-field-${testid}`}
          type={htmlType}
          value={dialog?.draft[key] ?? ""}
          onChange={(e) => updateDraft(key, e.target.value)}
          className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
        />
      </div>
    );
  }

  return (
    <div>
      {mismatchNotice && (
        <div
          data-testid="personal-info-mismatch-notice"
          className="mb-3 rounded-lg border border-critical/40 bg-critical-container px-3 py-2 text-sm text-critical"
        >
          {t("entryEditor.mismatchNotice")}
        </div>
      )}

      <button
        type="button"
        data-testid="personal-info-edit"
        onClick={openDialog}
        className="mt-2 rounded-lg border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container"
      >
        {t("personalInfoEditor.editButton")}
      </button>

      {dialog &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("personalInfoEditor.dialogTitle")}
            data-testid="personal-info-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") closeDialog();
            }}
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden" />
              <h3 className="mb-4 text-base font-bold text-on-surface">{t("personalInfoEditor.dialogTitle")}</h3>

              <div className="space-y-3">
                {textField("name", "name", t("personalInfoEditor.fieldName"))}
                {textField("email", "email", t("personalInfoEditor.fieldEmail"), "email")}
                {textField("phone", "phone", t("personalInfoEditor.fieldPhone"))}
                {textField("location", "location", t("personalInfoEditor.fieldLocation"))}
                {textField("address", "address", t("personalInfoEditor.fieldAddress"))}
                {textField("nationality", "nationality", t("personalInfoEditor.fieldNationality"))}
                <div>
                  <label
                    htmlFor="personal-info-date-of-birth"
                    className="mb-1 block text-xs font-medium text-on-surface-variant"
                  >
                    {t("personalInfoEditor.fieldDateOfBirth")}
                  </label>
                  <input
                    id="personal-info-date-of-birth"
                    data-testid="personal-info-field-date-of-birth"
                    type="text"
                    placeholder={t("personalInfoEditor.dateOfBirthPlaceholder")}
                    value={dialog?.draft.date_of_birth ?? ""}
                    onChange={(e) => updateDraft("date_of_birth", e.target.value)}
                    className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                  />
                </div>
                {textField("linkedin_url", "linkedin-url", t("personalInfoEditor.fieldLinkedin"))}
                {textField("xing_url", "xing-url", t("personalInfoEditor.fieldXing"))}
                {textField("website_url", "website-url", t("personalInfoEditor.fieldWebsite"))}

                {staleNotice && (
                  <p
                    data-testid="personal-info-stale-notice"
                    className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
                  >
                    {t("entryEditor.staleNotice")}
                  </p>
                )}
                {validationError && (
                  <p className="text-sm text-critical" data-testid="personal-info-validation-error">
                    {validationError}
                  </p>
                )}
                {dialogError && (
                  <p className="text-sm text-critical" data-testid="personal-info-dialog-error">
                    {dialogError}
                  </p>
                )}
              </div>

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="personal-info-cancel"
                  onClick={closeDialog}
                  disabled={saving}
                  className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {tCommon("cancel")}
                </button>
                <button
                  type="button"
                  data-testid="personal-info-save"
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
