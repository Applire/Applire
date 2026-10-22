// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Display-only helpers for My Profile (Hazelnut build 3, WP-V). The stored
 * value is NEVER rewritten — the CV keeps the CV's language and its date
 * precision; these only decide what the screen shows in the UI language.
 */

export type DisplayLocale = "de" | "en";

/**
 * Display copy of the 24 DE<->EN language-name pairs in
 * backend/applire/services/profile/language_names.py (LANGUAGE_NAME_DE_EN) —
 * one table for the codebase (ADR-066); keep the two in step.
 */
const LANGUAGE_NAME_DE_EN: Record<string, string> = {
  deutsch: "german", englisch: "english", französisch: "french",
  spanisch: "spanish", italienisch: "italian", polnisch: "polish",
  türkisch: "turkish", russisch: "russian", niederländisch: "dutch",
  portugiesisch: "portuguese", arabisch: "arabic", chinesisch: "chinese",
  japanisch: "japanese", koreanisch: "korean", hindi: "hindi",
  schwedisch: "swedish", dänisch: "danish", norwegisch: "norwegian",
  finnisch: "finnish", tschechisch: "czech", ungarisch: "hungarian",
  rumänisch: "romanian", griechisch: "greek", ukrainisch: "ukrainian",
};
const DE_BY_EN: Record<string, string> = Object.fromEntries(
  Object.entries(LANGUAGE_NAME_DE_EN).map(([de, en]) => [en, de]),
);

const fold = (s: string) => s.normalize("NFC").trim().toLocaleLowerCase("en");
const capitalise = (s: string) => s.charAt(0).toLocaleUpperCase() + s.slice(1);

/** The language name in the UI language when the WHOLE name is a table entry. */
export function localizedLanguageName(name: string, locale: DisplayLocale): string {
  const f = fold(name);
  const en = f in LANGUAGE_NAME_DE_EN ? LANGUAGE_NAME_DE_EN[f] : f in DE_BY_EN ? f : null;
  if (en === null) return name;
  const target = locale === "en" ? en : DE_BY_EN[en];
  if (!target || f === target) return name;
  return capitalise(target);
}

/** [German, English] level words; whole-token, case-insensitive match on either side. */
const LEVEL_WORDS: ReadonlyArray<readonly [string, string, ...string[]]> = [
  ["Muttersprache", "Native", "native or bilingual", "mother tongue", "native language", "bilingual"],
  ["Muttersprachlich", "Native speaker", "muttersprachler"],
  ["Fließend", "Fluent", "full professional"],
  ["Verhandlungssicher", "Business fluent", "professional working"],
  ["Sehr gut", "Very good"],
  ["Sehr gute Kenntnisse", "Very good command"],
  ["Gut", "Good"],
  ["Gute Kenntnisse", "Good command"],
  ["Grundkenntnisse", "Basic knowledge", "basic", "elementary"],
  ["Grundlagen", "Basic"],
  ["Fortgeschritten", "Advanced"],
  ["Mittelstufe", "Intermediate"],
  ["Anfänger", "Beginner"],
];
const LEVEL_BY_FOLD = new Map<string, readonly [string, string]>();
for (const [de, en, ...synonyms] of LEVEL_WORDS) {
  for (const w of [de, en, ...synonyms]) {
    if (!LEVEL_BY_FOLD.has(fold(w))) LEVEL_BY_FOLD.set(fold(w), [de, en]);
  }
}

// Separators between level parts: "fließend, C1", "B2 / C1", "C1 (fluent)", "A - B".
const LEVEL_SPLIT = /(\s*[,;/()]\s*|\s+[-–—]\s+)/;

function translateLevelToken(token: string, locale: DisplayLocale): string {
  const row = LEVEL_BY_FOLD.get(fold(token));
  if (!row) return token; // CEFR codes and the candidate's own words
  const target = locale === "de" ? row[0] : row[1];
  if (fold(target) === fold(token)) return token;
  // German nouns keep their capital whatever the source casing was.
  if (locale === "de" && /^Muttersprache$|kenntnisse$|^Grundlagen$|^Anfänger$/.test(target)) return target;
  const lowerStart = token.charAt(0) === token.charAt(0).toLocaleLowerCase() && /\p{L}/u.test(token.charAt(0));
  return lowerStart ? target.charAt(0).toLocaleLowerCase() + target.slice(1) : target;
}

/** The level in the UI language: known words translated, CEFR codes and unknown text kept. */
export function localizedLanguageLevel(level: string, locale: DisplayLocale): string {
  return level
    .split(LEVEL_SPLIT)
    .map((part, i) => (i % 2 === 1 ? part : translateLevelToken(part, locale)))
    .join("");
}

const YEAR = /^(\d{4})$/;
const YEAR_MONTH = /^(\d{4})-(\d{2})$/;
const YEAR_MONTH_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * A stored partial date at the precision it was stored: "2022" -> "2022",
 * "2021-03" -> localized month + year, a full date -> localized date. A value
 * that is not one of those shapes is returned as stored. "YYYY-01-01" legacy
 * rows are shown as the full date they are — a padded value cannot be told
 * from a real 1 January.
 */
export function formatDisplayDate(value: string | null | undefined, locale: DisplayLocale): string {
  if (value === null || value === undefined) return "";
  const s = value.trim();
  if (YEAR.test(s)) return s;
  const ym = YEAR_MONTH.exec(s);
  const ymd = YEAR_MONTH_DAY.exec(s);
  const m = ym ?? ymd;
  if (!m) return value;
  const [year, month, day] = [Number(m[1]), Number(m[2]), ymd ? Number(m[3]) : 1];
  const d = new Date(Date.UTC(year, month - 1, day));
  if (month < 1 || month > 12 || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return value;
  const opts: Intl.DateTimeFormatOptions = ymd
    ? locale === "de"
      ? { day: "2-digit", month: "2-digit", year: "numeric" }
      : { day: "numeric", month: "short", year: "numeric" }
    : { month: "short", year: "numeric" };
  return new Intl.DateTimeFormat(locale, { ...opts, timeZone: "UTC" }).format(d);
}
