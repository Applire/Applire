// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * D-B3V7 (ADR-063 amended 2026-10-22; rulings 787:L3 + V-4) — the notice
 * period / earliest start on My Profile.
 *
 * Shows the value with "Stand: <date>" (when it was recorded) and edits it
 * through `PUT /api/profile/availability` — the one write door; an empty save
 * clears it. When nothing is recorded the card shows no value and no date,
 * only the way to add one (the dashboard reminder links here: `#availability`).
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { formatDisplayDate } from "@/lib/profileDisplay";

export interface AvailabilityValue {
  text?: string | null;
  recorded_at?: string | null;
  source?: string | null;
  reminder_dismissed_at?: string | null;
}

const MAX_CHARS = 500;

export function AvailabilityCard({
  value,
  apiBase,
  locale,
  onProfileUpdated,
}: {
  value: AvailabilityValue | null | undefined;
  apiBase: string;
  locale: "de" | "en";
  onProfileUpdated: (updated: unknown) => void;
}) {
  const t = useTranslations("profile.availability");
  const text = (value?.text ?? "").trim();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  function startEdit() {
    setDraft(text);
    setFailed(false);
    setEditing(true);
  }

  async function save() {
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch(`${apiBase}/api/profile/availability`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: draft.trim() }),
      });
      if (!res.ok) throw new Error(String(res.status));
      onProfileUpdated(await res.json());
      setEditing(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="availability-card">
      <h3 className="font-heading text-base font-semibold text-neutral-dark">{t("title")}</h3>
      {!editing && text && (
        <div className="mt-1 text-sm">
          <p data-testid="availability-text" className="break-words text-on-surface">
            {text}
          </p>
          {value?.recorded_at && (
            <p data-testid="availability-recorded" className="mt-0.5 text-xs text-on-surface-variant">
              {t("recordedAt", { date: formatDisplayDate(value.recorded_at, locale) })}
            </p>
          )}
        </div>
      )}
      {!editing && (
        <button
          type="button"
          data-testid="availability-edit"
          onClick={startEdit}
          className="mt-2 text-sm font-medium text-primary hover:underline"
        >
          {text ? t("edit") : t("add")}
        </button>
      )}
      {editing && (
        <div className="mt-2 space-y-2">
          <label htmlFor="availability-input" className="block text-xs text-on-surface-variant">
            {t("label")}
          </label>
          <input
            id="availability-input"
            data-testid="availability-input"
            type="text"
            value={draft}
            maxLength={MAX_CHARS}
            placeholder={t("placeholder")}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded-md border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
          />
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="availability-save"
              disabled={busy}
              onClick={() => void save()}
              className="rounded-full bg-primary px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
            >
              {t("save")}
            </button>
            <button
              type="button"
              data-testid="availability-cancel"
              disabled={busy}
              onClick={() => setEditing(false)}
              className="rounded-full border border-outline-variant bg-white px-3 py-1.5 text-sm text-on-surface"
            >
              {t("cancel")}
            </button>
            {failed && (
              <span role="alert" className="text-xs text-red-700">
                {t("saveFailed")}
              </span>
            )}
          </div>
          {text && <p className="text-xs text-on-surface-variant">{t("clearHint")}</p>}
        </div>
      )}
    </div>
  );
}
