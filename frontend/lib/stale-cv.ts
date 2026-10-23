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

// E039/US221 (journey Branch H) — stale-CV indicator helpers.
//
// The backend's stale_cv read model says the Master Profile grew after the
// application's newest CV was tailored, with a per-section `gained` delta.
// These helpers turn that delta into the human explanation the nudge shows
// ("Fähigkeiten +3, Berufserfahrung +1") and into a compact query param so the
// explanation survives the navigation to the flow CV page where the freshly
// re-tailored version lands.

export interface StaleCVGained {
  section: string;
  count: number;
}

export interface StaleCVInfo {
  latest_cv_id: string;
  latest_cv_created_at: string;
  latest_cv_template: string;
  profile_enriched_at: string;
  gained: StaleCVGained[];
  // E042/US239 (ADR-051 amendment §4): the pinned/newest ready CV's persisted
  // page target, so one-click re-tailor can forward the same target. null on
  // legacy/pre-E042 rows.
  target_pages: number | null;
}

/**
 * Enrichment-trail section → `profile` namespace label key. Covers every
 * section the backend counts as growth (`CV_FEEDING_SECTIONS` in
 * `services/application.py` — a vitest reads that set and checks each has a
 * key here). `summary` stays for older trail records.
 */
export const SECTION_LABEL_KEYS: Record<string, string> = {
  personal_info: "sectionPersonalInfo",
  professional_summary: "sectionSummary",
  summary: "sectionSummary",
  work_experience: "sectionWorkExperience",
  education: "sectionEducation",
  skills: "sectionSkills",
  languages: "sectionLanguages",
  certifications: "sectionCertifications",
  projects: "sectionProjects",
  publications: "sectionPublications",
  volunteer_activities: "sectionVolunteer",
  signature_stories: "sectionSignatureStories",
};

/**
 * "Fähigkeiten +3, Berufserfahrung +1" — `resolve` translates a
 * `profile`-namespace key. A section without a label is left out (re-probe N3:
 * "availability +1" reached the nudge as a raw field name); when nothing is
 * left, the caller shows its plain sentence.
 */
export function formatGained(
  gained: StaleCVGained[],
  resolve: (key: string) => string,
): string {
  return gained
    .filter((g) => SECTION_LABEL_KEYS[g.section] !== undefined)
    .map((g) => `${resolve(SECTION_LABEL_KEYS[g.section])} +${g.count}`)
    .join(", ");
}

/** Compact `section:count` list for the ?retailored= query param. */
export function encodeGained(gained: StaleCVGained[]): string {
  return gained.map((g) => `${g.section}:${g.count}`).join(",");
}

/**
 * Strict inverse of encodeGained: anything that isn't a plain
 * `[a-z0-9_]+:<int>` list decodes to [] — the param crosses a URL, so garbage
 * renders as "no delta", never as content.
 */
export function decodeGained(param: string | null): StaleCVGained[] {
  if (!param) return [];
  const out: StaleCVGained[] = [];
  for (const part of param.split(",")) {
    const m = /^([a-z0-9_]+):(\d+)$/.exec(part);
    if (!m) return [];
    out.push({ section: m[1], count: Number(m[2]) });
  }
  return out;
}
