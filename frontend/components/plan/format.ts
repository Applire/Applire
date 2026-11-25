// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Price and date formats of the plan dialogs. Prices are built by hand from cents
 * (not Intl) so the bytes match the approved mock: DE `9,90 €`, EN `€9.90`.
 */

export function formatPrice(cents: number, locale: string): string {
  const abs = Math.abs(cents);
  const whole = Math.floor(abs / 100);
  const frac = String(abs % 100).padStart(2, "0");
  const sign = cents < 0 ? "-" : "";
  return locale === "de" ? `${sign}${whole},${frac} €` : `${sign}€${whole}.${frac}`;
}

const tag = (locale: string) => (locale === "de" ? "de-DE" : "en-GB");

/** An ISO `YYYY-MM-DD` as a LOCAL date (never UTC: no day shift). */
function localDate(iso: string, monthOffset = 0): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1 + monthOffset, d);
}

/** "1. Dezember" / "1 December". */
export function formatDay(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(tag(locale), { day: "numeric", month: "long" }).format(localDate(iso));
}

/** The month of the date itself: "Dezember" / "December" (the downgrade button). */
export function formatMonthOf(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(tag(locale), { month: "long" }).format(localDate(iso));
}

/** The month BEFORE the renewal date: renews 1 Dec → "November" (board A4 title). */
export function formatMonthBefore(renewsOn: string, locale: string): string {
  return new Intl.DateTimeFormat(tag(locale), { month: "long" }).format(localDate(renewsOn, -1));
}
