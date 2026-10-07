// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Small display formatters shared by the account card, the tokens tables and the
 * admin users list (Strawberry 2b). The words ("heute", "gestern", "vor 4 Minuten",
 * "noch nie") come from the `account` catalog; numbers and dates from `Intl` in the
 * UI locale — never `toLocaleString()` without a locale, which formats by the
 * BROWSER locale (the ops panel learnt that on a pixel screenshot, 2026-09-09).
 */

type T = (key: string, values?: Record<string, string | number>) => string;

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/**
 * "gerade eben" · "vor 4 Minuten" (< 1 h) · "heute, 09:12" · "gestern, 21:40" ·
 * "12.08.2026" — the forms the W0-B mocks show. `t` is `useTranslations("account")`.
 */
export function formatWhen(iso: string, locale: string, t: T, now: Date = new Date()): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  const diffMs = now.getTime() - when.getTime();
  if (diffMs >= 0 && diffMs < 60_000) return t("justNow");
  if (diffMs >= 0 && diffMs < 3_600_000) return t("minutesAgo", { count: Math.floor(diffMs / 60_000) });
  const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(when);
  if (sameDay(when, now)) return t("todayAt", { time });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(when, yesterday)) return t("yesterdayAt", { time });
  return formatDate(iso, locale);
}

/** "03.10.2026" / "10/03/2026" — a plain date in the UI locale. */
export function formatDate(iso: string, locale: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  return new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(when);
}

/** "212 MB" — whole megabytes (the users list's storage column). */
export function formatMegabytes(bytes: number, locale: string): string {
  const mb = Math.round(bytes / (1024 * 1024));
  return new Intl.NumberFormat(locale, { style: "unit", unit: "megabyte", maximumFractionDigits: 0 }).format(mb);
}

/** "1,8 Mio." / "420 Tsd." / "1.8M" — compact token counts for the AI-usage column. */
export function formatCompact(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}
