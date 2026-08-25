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
 * US290 — shared types + tiny display helpers for the structured
 * work_experience/education editors. Kept independent of React so the shape
 * is reusable from both the editor components and their tests.
 *
 * Field lists mirror the backend Pydantic schemas (`WorkEntry`/`EducationEntry`)
 * verbatim; an index signature keeps any unknown/legacy key (e.g. a
 * hand-edited record's stray field) round-tripping through the spread-based
 * form state instead of being silently dropped (H1.3).
 */

export interface WorkEntry {
  /** Absent on a NEW entry — the backend mints the id (H1.1). */
  id?: string;
  company: string;
  role: string;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  /** Tri-state: null = unknown, true = current, false = ended (H1.10). */
  is_current?: boolean | null;
  responsibilities?: string[];
  achievements?: string[];
  technologies?: string[];
  role_aliases?: string[];
  industry_context?: string | null;
  team_size?: number | null;
  budget_managed?: string | null;
  /** Derived by the backend — never recomputed on this door. */
  expected_fields?: string[] | null;
  /** Derived by the backend — echoed back untouched. */
  role_fact_projections?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface EducationEntry {
  /** Absent on a NEW entry — the backend mints the id (H1.1). */
  id?: string;
  institution: string;
  degree: string;
  field?: string;
  start_date?: string | null;
  end_date?: string | null;
  grade?: string | null;
  thesis_title?: string | null;
  relevant_coursework?: string[];
  /** Legacy records may carry this instead of start_date/end_date. */
  year?: string;
  [key: string]: unknown;
}

/** The shape both GET /api/profile and PATCH /api/profile/{section} return. */
export interface ProfileSectionsResponse {
  updated_at: string;
  profile: {
    work_experience?: WorkEntry[];
    education?: EducationEntry[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export function nonEmptyText(s?: string | null): s is string {
  return typeof s === "string" && s.trim().length > 0;
}

/**
 * Mirrors `ProfileSectionCard.tsx`'s private `formatPeriod` exactly (kept
 * as a separate copy rather than an export change to that shared,
 * heavily-tested file — see US290 report for the reasoning).
 */
export function formatEntryPeriod(
  start?: string | null,
  end?: string | null,
  presentLabel?: string,
  isCurrent?: boolean | null,
): string | null {
  if (!nonEmptyText(start) && !nonEmptyText(end)) return null;
  const left = nonEmptyText(start) ? start : null;
  const explicitEnd = nonEmptyText(end) ? end : null;
  const right = isCurrent === true ? (presentLabel ?? explicitEnd) : (explicitEnd ?? presentLabel ?? null);
  if (!left) return right;
  if (!right) return left;
  return `${left} → ${right}`;
}

/** A brand-new work entry — deliberately carries no `id` key (H1.1). */
export function makeEmptyWorkEntry(): WorkEntry {
  return {
    company: "",
    role: "",
    location: null,
    start_date: null,
    end_date: null,
    is_current: null,
    responsibilities: [],
    achievements: [],
    technologies: [],
    role_aliases: [],
    industry_context: null,
    team_size: null,
    budget_managed: null,
    expected_fields: null,
    role_fact_projections: {},
  };
}

/** A brand-new education entry — deliberately carries no `id` key (H1.1). */
export function makeEmptyEducationEntry(): EducationEntry {
  return {
    institution: "",
    degree: "",
    field: "",
    start_date: null,
    end_date: null,
    grade: null,
    thesis_title: null,
    relevant_coursework: [],
  };
}

/** Deep-clones a JSON-safe entry so an open dialog never mutates the list prop. */
export function cloneEntry<T>(entry: T): T {
  return JSON.parse(JSON.stringify(entry)) as T;
}
