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

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { isoUtcToLocalInput, localInputToIsoUtc } from "@/lib/deadline-datetime";
import { patchApplication } from "@/lib/api/applications";
import type { ApplicationDetail } from "@/app/(shell)/applications/[appId]/page";

/**
 * Only the key(s) a tracking-sidebar save actually wrote, plus the fresh
 * updated_at. The page spreads exactly this onto its `application` state —
 * never the whole PATCH response — so an out-of-order response for field A
 * can never transiently overwrite a newer value of field B.
 */
export type TrackingSavedPatch = Partial<
  Pick<ApplicationDetail, "deadline" | "source_url" | "notes">
> & { updated_at: string };

interface DossierTrackingSidebarProps {
  application: ApplicationDetail;
  /** Called with ONLY the saved field + updated_at after a successful save. */
  onSaved: (patch: TrackingSavedPatch) => void;
  onError: (message: string) => void;
}

type FieldStatus = "idle" | "saving" | "saved" | "error";

const NOTES_DEBOUNCE_MS = 800;

interface UseFieldAutosaveOptions {
  initial: string;
  /** Normalize before the dirty-check and the save (e.g. trim). */
  normalize?: (v: string) => string;
  /** PATCH the candidate value; resolve to the field-scoped saved patch. */
  save: (candidate: string) => Promise<TrackingSavedPatch>;
  onSaved: (patch: TrackingSavedPatch) => void;
  onError: () => void;
  /** When set, the save fires debounced after the last change (notes);
   * otherwise the caller triggers it via `flush()` on blur. */
  debounceMs?: number;
}

/**
 * Per-field autosave state machine, shared by all three sidebar fields.
 *
 * Edit-generation guard: `genRef` is bumped on every keystroke, and a save
 * captures the generation at fire time. When the request settles, anything
 * that would stomp a NEWER edit is gated on the generation still matching:
 * a FAILED save only reverts the input if the user hasn't typed since it
 * fired (their newer text survives; the inline error still shows), and a
 * successful save only re-normalizes/flips to "saved" on a match (otherwise
 * the field is dirty again → back to "idle"; the next blur/debounce fires
 * the follow-up save). The baseline always advances on success — the server
 * did persist the candidate — so the follow-up dirty-check stays correct.
 */
function useFieldAutosave({
  initial,
  normalize,
  save,
  onSaved,
  onError,
  debounceMs,
}: UseFieldAutosaveOptions) {
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [status, setStatus] = useState<FieldStatus>("idle");
  const genRef = useRef(0);

  const handleChange = (v: string) => {
    genRef.current += 1;
    setValue(v);
    // Keep "saving" visible while a request is in flight — only a SETTLED
    // saved/error state is cleared by resumed typing.
    setStatus((s) => (s === "saving" ? s : "idle"));
  };

  const flush = async () => {
    const candidate = normalize ? normalize(value) : value;
    if (candidate === baseline) return;
    const gen = genRef.current;
    const revertTo = baseline;
    setStatus("saving");
    try {
      const patch = await save(candidate);
      setBaseline(candidate);
      if (genRef.current === gen) {
        setValue(candidate); // apply normalization (e.g. trimmed URL)
        setStatus("saved");
      } else {
        setStatus("idle"); // re-edited mid-flight — dirty again, no indicator
      }
      onSaved(patch);
    } catch {
      if (genRef.current === gen) setValue(revertTo);
      setStatus("error");
      onError();
    }
  };

  // Debounced autosave (notes): fire ~debounceMs after the last change.
  // `baseline` is a dependency on purpose — a success that advances the
  // baseline re-arms the effect, so text typed mid-flight gets its own save.
  useEffect(() => {
    if (debounceMs === undefined) return;
    if ((normalize ? normalize(value) : value) === baseline) return;
    const handle = setTimeout(() => void flush(), debounceMs);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, baseline]);

  return { value, status, handleChange, flush };
}

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
  const reportError = () => onError(t("saveFailed"));

  const deadline = useFieldAutosave({
    initial: isoUtcToLocalInput(application.deadline ?? ""),
    save: async (candidate) => {
      const iso = localInputToIsoUtc(candidate); // "" for empty/invalid input
      const r = await patchApplication(application.id, { deadline: iso || null });
      return { deadline: r.deadline, updated_at: r.updated_at };
    },
    onSaved,
    onError: reportError,
  });

  const source = useFieldAutosave({
    initial: application.source_url ?? "",
    normalize: (v) => v.trim(),
    save: async (candidate) => {
      const r = await patchApplication(application.id, { source_url: candidate || null });
      return { source_url: r.source_url, updated_at: r.updated_at };
    },
    onSaved,
    onError: reportError,
  });

  const notes = useFieldAutosave({
    initial: application.notes ?? "",
    save: async (candidate) => {
      const r = await patchApplication(application.id, { notes: candidate || null });
      return { notes: r.notes, updated_at: r.updated_at };
    },
    onSaved,
    onError: reportError,
    debounceMs: NOTES_DEBOUNCE_MS,
  });

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
            value={deadline.value}
            onChange={(e) => deadline.handleChange(e.target.value)}
            onBlur={() => void deadline.flush()}
            data-testid="dossier-tracking-deadline"
            error={deadline.status === "error"}
          />
          <FieldFeedback t={t} status={deadline.status} testIdPrefix="dossier-tracking-deadline" />
        </div>

        <div>
          <label className="block text-sm font-medium text-on-surface-variant mb-1">{t("sourceLink")}</label>
          <Input
            type="url"
            value={source.value}
            onChange={(e) => source.handleChange(e.target.value)}
            onBlur={() => void source.flush()}
            placeholder={t("sourceLinkPlaceholder")}
            data-testid="dossier-tracking-source"
            error={source.status === "error"}
          />
          <FieldFeedback t={t} status={source.status} testIdPrefix="dossier-tracking-source" />
        </div>

        <div>
          <label className="block text-sm font-medium text-on-surface-variant mb-1">{t("notes")}</label>
          <textarea
            className={cn(
              "w-full rounded-lg border bg-white px-4 py-2 text-sm min-h-[100px] focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20",
              notes.status === "error" ? "border-critical" : "border-outline-variant"
            )}
            value={notes.value}
            onChange={(e) => notes.handleChange(e.target.value)}
            placeholder={t("notesPlaceholder")}
            data-testid="dossier-tracking-notes"
          />
          <FieldFeedback t={t} status={notes.status} testIdPrefix="dossier-tracking-notes" />
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
  testIdPrefix,
}: {
  t: ReturnType<typeof useTranslations>;
  status: FieldStatus;
  testIdPrefix: string;
}) {
  if (status === "saving") {
    return (
      <p data-testid={`${testIdPrefix}-saving`} className="text-xs text-on-surface-variant mt-1">
        {t("saving")}
      </p>
    );
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
        {t("saveFailed")}
      </p>
    );
  }
  return null;
}
