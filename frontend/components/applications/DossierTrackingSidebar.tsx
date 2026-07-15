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

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { isoUtcToLocalInput, localInputToIsoUtc } from "@/lib/deadline-datetime";
import { patchApplication, type ApplicationPatchResponse } from "@/lib/api/applications";
import type { ApplicationDetail } from "@/app/(shell)/applications/[appId]/page";

interface DossierTrackingSidebarProps {
  application: ApplicationDetail;
  /** Called with the PATCH response after a successful save — the page
   * applies it to its `application` state (no full refetch needed; each
   * save touches at most one field). */
  onSaved: (patch: ApplicationPatchResponse) => void;
  onError: (message: string) => void;
}

type FieldStatus = "idle" | "saving" | "saved" | "error";

const NOTES_DEBOUNCE_MS = 800;

/**
 * Tracking sidebar (E041/US234, closes #164) — deadline/source link/notes,
 * each saved independently and only when it actually changed. Replaces the
 * deleted Status Management/Details cards' bottom-Save pattern: no page-level
 * save state, no re-sending untouched fields.
 *
 * #164 fix: the deleted old code loaded `data.deadline.slice(0, 16)` (UTC
 * digits straight into a local `datetime-local` input) but saved with
 * `new Date(value).toISOString()` (interprets those same digits as LOCAL
 * time) — each round trip sheared the UTC offset off the deadline. Both
 * directions here go through `lib/deadline-datetime.ts`'s symmetric
 * converters, so 09:30 typed stays 09:30 after any number of reloads.
 *
 * Field state is seeded once from the `application` prop at mount (the page
 * never remounts this component for in-place updates to OTHER fields, e.g.
 * status changes or pin PATCHes elsewhere on the cockpit, so there is no
 * risk of an unrelated re-render stomping an in-progress edit here).
 */
export function DossierTrackingSidebar({ application, onSaved, onError }: DossierTrackingSidebarProps) {
  const t = useTranslations("applications");

  const [deadlineValue, setDeadlineValue] = useState(() => isoUtcToLocalInput(application.deadline ?? ""));
  const [deadlineBaseline, setDeadlineBaseline] = useState(deadlineValue);
  const [deadlineStatus, setDeadlineStatus] = useState<FieldStatus>("idle");
  const [deadlineError, setDeadlineError] = useState("");

  const [sourceValue, setSourceValue] = useState(application.source_url ?? "");
  const [sourceBaseline, setSourceBaseline] = useState(sourceValue);
  const [sourceStatus, setSourceStatus] = useState<FieldStatus>("idle");
  const [sourceError, setSourceError] = useState("");

  const [notesValue, setNotesValue] = useState(application.notes ?? "");
  const [notesBaseline, setNotesBaseline] = useState(notesValue);
  const [notesStatus, setNotesStatus] = useState<FieldStatus>("idle");
  const [notesError, setNotesError] = useState("");

  async function handleDeadlineBlur() {
    if (deadlineValue === deadlineBaseline) return;
    const payloadValue = deadlineValue ? localInputToIsoUtc(deadlineValue) : null;
    setDeadlineStatus("saving");
    setDeadlineError("");
    try {
      const result = await patchApplication(application.id, { deadline: payloadValue });
      setDeadlineBaseline(deadlineValue);
      setDeadlineStatus("saved");
      onSaved(result);
    } catch {
      setDeadlineValue(deadlineBaseline);
      setDeadlineStatus("error");
      setDeadlineError(t("saveFailed"));
      onError(t("saveFailed"));
    }
  }

  async function handleSourceBlur() {
    const trimmed = sourceValue.trim();
    if (trimmed === sourceBaseline) return;
    setSourceStatus("saving");
    setSourceError("");
    try {
      const result = await patchApplication(application.id, { source_url: trimmed || null });
      setSourceValue(trimmed);
      setSourceBaseline(trimmed);
      setSourceStatus("saved");
      onSaved(result);
    } catch {
      setSourceValue(sourceBaseline);
      setSourceStatus("error");
      setSourceError(t("saveFailed"));
      onError(t("saveFailed"));
    }
  }

  // Notes autosave debounced ~800ms after the last keystroke — too low-value
  // to save on blur only (a long note is easy to lose on an accidental tab
  // switch), too frequent to save on every keystroke.
  useEffect(() => {
    if (notesValue === notesBaseline) return;
    const handle = setTimeout(() => {
      setNotesStatus("saving");
      setNotesError("");
      patchApplication(application.id, { notes: notesValue || null })
        .then((result) => {
          setNotesBaseline(notesValue);
          setNotesStatus("saved");
          onSaved(result);
        })
        .catch(() => {
          setNotesValue(notesBaseline);
          setNotesStatus("error");
          setNotesError(t("saveFailed"));
          onError(t("saveFailed"));
        });
    }, NOTES_DEBOUNCE_MS);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notesValue]);

  const footerParts = [
    t("trackingCreatedLine", { date: new Date(application.created_at).toLocaleDateString() }),
    application.applied_at
      ? t("trackingAppliedLine", { date: new Date(application.applied_at).toLocaleDateString() })
      : null,
    t("trackingUpdatedLine", { date: new Date(application.updated_at).toLocaleDateString() }),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Card className="p-6" data-testid="dossier-tracking-sidebar">
      <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">{t("trackingZoneTitle")}</h2>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-on-surface-variant mb-1">{t("deadline")}</label>
          <Input
            type="datetime-local"
            value={deadlineValue}
            onChange={(e) => {
              setDeadlineValue(e.target.value);
              if (deadlineStatus !== "idle") setDeadlineStatus("idle");
            }}
            onBlur={() => void handleDeadlineBlur()}
            data-testid="dossier-tracking-deadline"
            error={deadlineStatus === "error"}
          />
          <FieldFeedback
            t={t}
            status={deadlineStatus}
            errorMessage={deadlineError}
            testIdPrefix="dossier-tracking-deadline"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-on-surface-variant mb-1">{t("sourceLink")}</label>
          <Input
            type="url"
            value={sourceValue}
            onChange={(e) => {
              setSourceValue(e.target.value);
              if (sourceStatus !== "idle") setSourceStatus("idle");
            }}
            onBlur={() => void handleSourceBlur()}
            placeholder={t("sourceLinkPlaceholder")}
            data-testid="dossier-tracking-source"
            error={sourceStatus === "error"}
          />
          <FieldFeedback
            t={t}
            status={sourceStatus}
            errorMessage={sourceError}
            testIdPrefix="dossier-tracking-source"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-on-surface-variant mb-1">{t("notes")}</label>
          <textarea
            className={cn(
              "w-full rounded-lg border bg-white px-4 py-2 text-sm min-h-[100px] focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20",
              notesStatus === "error" ? "border-critical" : "border-outline-variant"
            )}
            value={notesValue}
            onChange={(e) => {
              setNotesValue(e.target.value);
              if (notesStatus !== "idle") setNotesStatus("idle");
            }}
            placeholder={t("notesPlaceholder")}
            data-testid="dossier-tracking-notes"
          />
          <FieldFeedback t={t} status={notesStatus} errorMessage={notesError} testIdPrefix="dossier-tracking-notes" />
        </div>
      </div>

      <p
        data-testid="dossier-tracking-footer"
        className="text-xs text-on-surface-variant mt-4 pt-4 border-t border-outline-variant"
      >
        {footerParts}
      </p>
    </Card>
  );
}

/** Per-field saving/saved/error feedback — shared markup for all three fields. */
function FieldFeedback({
  t,
  status,
  errorMessage,
  testIdPrefix,
}: {
  t: ReturnType<typeof useTranslations>;
  status: FieldStatus;
  errorMessage: string;
  testIdPrefix: string;
}) {
  if (status === "saving") {
    return <p className="text-xs text-on-surface-variant mt-1">{t("saving")}</p>;
  }
  if (status === "saved") {
    return (
      <span
        data-testid={`${testIdPrefix}-status`}
        className="inline-flex items-center gap-1 text-xs text-success mt-1"
      >
        <Check className="w-3.5 h-3.5" aria-hidden="true" />
        {t("savedIndicator")}
      </span>
    );
  }
  if (status === "error") {
    return (
      <p data-testid={`${testIdPrefix}-error`} role="alert" className="text-xs text-critical mt-1">
        {errorMessage}
      </p>
    );
  }
  return null;
}
