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

/** Read-only provenance badge shown next to a skill/language/certification entry. */
export type EntryStatus = "confirmed" | "unconfirmed" | "denied";

/**
 * US291 — Skill/Language/Certification mirror the backend Pydantic schemas
 * verbatim (`Skill`/`Language`/`Certification`); an index signature keeps any
 * unknown/legacy key round-tripping through the spread-based form state
 * instead of being silently dropped (H1.3), exactly like WorkEntry/EducationEntry.
 */
export interface Skill {
  /** Absent on a NEW entry — the backend mints the id (H1.1). */
  id?: string;
  name: string;
  category?: "technical" | "soft" | "language" | "domain";
  proficiency?: "basic" | "intermediate" | "advanced" | "expert";
  years_experience?: number | null;
  /** Provenance of the number — NEVER editable on this door. */
  source?: string | null;
  last_used?: string | null;
  /** NEVER editable on this door. */
  experience_refs?: string[];
  /** Read-only badge — never a form control (H2.1). */
  status?: EntryStatus;
  [key: string]: unknown;
}

export interface Language {
  /** Absent on a NEW entry — the backend mints the id (H1.1). */
  id?: string;
  /** The vault's canonical field — NOT `name` (H2.4). */
  language: string;
  level?: string | null;
  /** Read-only badge — never a form control (H2.1). Languages are never "denied". */
  status?: "confirmed" | "unconfirmed";
  [key: string]: unknown;
}

export interface Certification {
  /** Absent on a NEW entry — the backend mints the id (H1.1). */
  id?: string;
  name: string;
  issuing_organization?: string | null;
  date_obtained?: string | null;
  expiry_date?: string | null;
  credential_id?: string | null;
  credential_url?: string | null;
  /** Read-only badge — never a form control (H2.1). Certifications are never "denied". */
  status?: "confirmed" | "unconfirmed";
  [key: string]: unknown;
}

/**
 * US292 — mirrors the backend `PersonalInfo` Pydantic schema verbatim.
 * `photo_url` is intentionally OMITTED here: it is owned by PhotoManager and
 * must never be read or written by PersonalInfoEditor's merge-patch (#178).
 */
export interface PersonalInfo {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  location?: string | null;
  address?: string | null;
  nationality?: string | null;
  /** Backend `date` field — accepts "DD.MM.YYYY"/"D.M.YYYY"/ISO, stores ISO. */
  date_of_birth?: string | null;
  linkedin_url?: string | null;
  xing_url?: string | null;
  website_url?: string | null;
  [key: string]: unknown;
}

/** The shape both GET /api/profile and PATCH /api/profile/{section} return. */
export interface ProfileSectionsResponse {
  updated_at: string;
  profile: {
    work_experience?: WorkEntry[];
    education?: EducationEntry[];
    skills?: Skill[];
    languages?: Language[];
    certifications?: Certification[];
    personal_info?: PersonalInfo;
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

/**
 * A brand-new skill/language/certification — deliberately carries no `id` key
 * (H1.1) and an EXPLICIT `status: "confirmed"` (H2.2, PO ruling 2026-08-25):
 * the user vouching for a fact they just typed is affirmative provenance,
 * not silence, so it is never left to default/omitted.
 */
export function makeEmptySkill(): Skill {
  return {
    name: "",
    category: "technical",
    proficiency: "intermediate",
    years_experience: null,
    last_used: null,
    status: "confirmed",
  };
}

export function makeEmptyLanguage(): Language {
  return {
    language: "",
    level: null,
    status: "confirmed",
  };
}

export function makeEmptyCertification(): Certification {
  return {
    name: "",
    issuing_organization: null,
    date_obtained: null,
    expiry_date: null,
    credential_id: null,
    credential_url: null,
    status: "confirmed",
  };
}

/** Deep-clones a JSON-safe entry so an open dialog never mutates the list prop. */
export function cloneEntry<T>(entry: T): T {
  return JSON.parse(JSON.stringify(entry)) as T;
}

/**
 * Trim every bullet and drop the empty ones — a whitespace-only bullet renders
 * as an invisible list item and is never what the user meant (adversarial
 * finding 2026-08-25). Pure, so the editors can normalise BEFORE validating.
 */
export function trimStringList(list: unknown): string[] {
  if (!Array.isArray(list)) return [];
  return list
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((item) => item.length > 0);
}
