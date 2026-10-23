// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * Ruling V-4 (founder 2026-10-22) — the notice period is never re-asked by the
 * interview; the dashboard reminds instead. The profile response carries ONE
 * answer, `availability_reminder: "missing" | "stale" | null` (V's
 * `services/profile/availability.py`, ADR-066: no reader re-derives it), and
 * this card shows it. "Nicht jetzt" / "Stimmt noch" closes it SERVER-side
 * (`POST /api/profile/availability/reminder/dismiss`) until the value changes
 * or another stale period passes — never localStorage. The action leads to the
 * My Profile edit (`/profile#availability`, V's anchor).
 *
 * Copy names the job (when could you start), never the mechanism (COPY.md).
 * A failed fetch shows nothing; a failed close hides the card for this visit
 * anyway — a reminder must never trap the dashboard.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

// Non-user-facing Material Symbols identifier — a JS const, not a JSX literal.
const ICON = "event_available";
export const AVAILABILITY_PROFILE_HREF = "/profile#availability";

type Reminder = "missing" | "stale";

interface ProfileReminderShape {
  availability_reminder?: Reminder | null;
  profile?: { availability?: { text?: string | null; recorded_at?: string | null } | null } | null;
}

export function AvailabilityReminderCard() {
  const t = useTranslations("dashboard.availabilityReminder");
  const locale = useLocale();
  const router = useRouter();
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [value, setValue] = useState<string | null>(null);
  const [recordedAt, setRecordedAt] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: ProfileReminderShape | null) => {
        if (cancelled || !d) return;
        const r = d.availability_reminder;
        if (r !== "missing" && r !== "stale") return;
        setReminder(r);
        setValue(d.profile?.availability?.text?.trim() || null);
        setRecordedAt(d.profile?.availability?.recorded_at ?? null);
      })
      .catch(() => {
        /* no reminder is better than a broken dashboard */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!reminder) return null;

  async function close() {
    setClosing(true);
    try {
      await fetch(`${API_BASE}/api/profile/availability/reminder/dismiss`, { method: "POST" });
    } catch {
      /* best-effort: the card still goes away for this visit */
    } finally {
      setReminder(null);
      setClosing(false);
    }
  }

  const formattedDate = recordedAt
    ? new Date(recordedAt).toLocaleDateString(locale === "de" ? "de-DE" : "en-GB", {
        day: "numeric",
        month: "long",
        year: "numeric",
      })
    : null;

  const body =
    reminder === "missing"
      ? t("missingBody")
      : value && formattedDate
        ? t("staleBody", { date: formattedDate, value })
        : t("staleBodyNoValue");

  return (
    <section
      data-testid="availability-reminder"
      data-reminder={reminder}
      aria-label={t("region")}
      className="mb-4 flex items-start gap-3 rounded-xl border border-primary/20 bg-primary-container px-4 py-3.5"
    >
      <span className="material-symbols-outlined shrink-0 text-primary" aria-hidden="true" style={{ fontSize: 22 }}>
        {ICON}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-bold text-on-surface">
          {reminder === "missing" ? t("missingTitle") : t("staleTitle")}
        </p>
        <p className="mt-1 text-[13px] leading-snug text-on-surface-variant">{body}</p>
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="availability-reminder-action"
            onClick={() => router.push(AVAILABILITY_PROFILE_HREF)}
            className="rounded-lg bg-primary px-3 py-1.5 text-[12px] font-bold text-white hover:opacity-90"
          >
            {reminder === "missing" ? t("missingAction") : t("staleAction")}
          </button>
          <button
            type="button"
            data-testid="availability-reminder-dismiss"
            onClick={() => void close()}
            disabled={closing}
            className="rounded-lg px-2 py-1.5 text-[12px] font-bold text-primary hover:bg-white/60 disabled:opacity-50"
          >
            {reminder === "missing" ? t("missingDismiss") : t("staleDismiss")}
          </button>
        </div>
      </div>
    </section>
  );
}
