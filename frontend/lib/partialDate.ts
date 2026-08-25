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

/**
 * US290 — pure date-shape helpers for the work/education entry editors
 * (H1.2/H1.9/H1.12). Deliberately independent of any DOM/React so the
 * Monat+Jahr round-trip and the legacy-verbatim rule are testable without
 * mounting a component.
 *
 * Canonical output is `YYYY-MM` when a month is known, `YYYY` when only the
 * year is known, and `null` when nothing is known — NEVER an empty string
 * and NEVER a default like "today".
 */

export interface PartialDate {
  year: number;
  /** 1-12, or null when only the year is known. */
  month: number | null;
}

/**
 * Parse result:
 * - `PartialDate` — a value the picker can represent (`YYYY` or `YYYY-MM`;
 *   `YYYY-MM-DD` is also accepted and truncated to year+month).
 * - `"legacy"` — a non-empty value the picker cannot parse (e.g. "Q3 2019",
 *   "2019/03"). Callers must preserve it verbatim (H1.12) rather than lose it.
 * - `null` — no value / unknown.
 */
export type ParsedPartialDate = PartialDate | "legacy" | null;

const YEAR_MONTH_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const YEAR_MONTH = /^(\d{4})-(\d{2})$/;
const YEAR_ONLY = /^(\d{4})$/;

export function parsePartialDate(value: string | null | undefined): ParsedPartialDate {
  if (value === null || value === undefined) return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;

  const dayMatch = YEAR_MONTH_DAY.exec(trimmed);
  if (dayMatch) {
    const year = Number(dayMatch[1]);
    const month = Number(dayMatch[2]);
    if (month >= 1 && month <= 12) return { year, month };
    return "legacy";
  }

  const monthMatch = YEAR_MONTH.exec(trimmed);
  if (monthMatch) {
    const year = Number(monthMatch[1]);
    const month = Number(monthMatch[2]);
    if (month >= 1 && month <= 12) return { year, month };
    return "legacy";
  }

  const yearMatch = YEAR_ONLY.exec(trimmed);
  if (yearMatch) {
    return { year: Number(yearMatch[1]), month: null };
  }

  return "legacy";
}

/** Formats a resolved (year, month) pair into the canonical wire shape. */
export function formatPartialDate(date: PartialDate): string {
  if (date.month === null) return String(date.year);
  return `${date.year}-${String(date.month).padStart(2, "0")}`;
}

/** True when `parsePartialDate` could not represent the value in the picker. */
export function isLegacyDate(value: string | null | undefined): boolean {
  return parsePartialDate(value) === "legacy";
}
