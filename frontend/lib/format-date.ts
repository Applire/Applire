// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * #789 (789:174 / 789:186): one date formatter for the UI language.
 *
 * A bare `toLocaleDateString()` formats by the BROWSER locale, so a German UI in
 * an en-US browser showed "Version vom 10/21/2026" and "Gespeichert bis
 * 12/22/2026". Every display date takes the UI locale (next-intl `useLocale()`)
 * through this helper. English maps to `en-GB` (day before month), the form the
 * My Documents table, the photo consent line and the availability card already
 * used (Hazelnut build 3, F2) — a European product, not a US one.
 */

/** The `Intl` locale tag for a UI language ("de" → "de-DE", anything else → "en-GB"). */
export function uiDateLocale(locale: string): string {
  return locale === "de" ? "de-DE" : "en-GB";
}

/**
 * A date in the UI language — "24.10.2026" / "24/10/2026" by default; pass
 * `options` for another shape (e.g. `{ day: "numeric", month: "short", year: "numeric" }`).
 * An unparseable value yields "" rather than "Invalid Date".
 */
export function formatUiDate(
  value: string | number | Date,
  locale: string,
  options?: Intl.DateTimeFormatOptions,
): string {
  const when = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleDateString(uiDateLocale(locale), options);
}
