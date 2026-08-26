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
 * US165/F3b — map a Health-hub issue's `field_ref` to the profile section that
 * holds the data, so "Resolve" can bring that section (and its structured
 * editor) into view. `field_ref` is one of three shapes from the backend
 * (`services/profile/health.py`): a bare conflict field name (`start_date`),
 * a dotted path (`work_experience.budget_managed`), or a comma-joined list of
 * section names from an accuracy issue (`projects, skills`).
 *
 * Exact section names win over substring guesses — an adversarial pass
 * (2026-08-26) found the substring-only version routing every `projects`,
 * `publications` and `volunteer_activities` issue to `skills` (the default)
 * or, via "date", to `work_experience`.
 */
export type ProfileSectionKey =
  | "personal_info"
  | "professional_summary"
  | "work_experience"
  | "education"
  | "skills"
  | "languages"
  | "certifications"
  | "projects"
  | "publications"
  | "volunteer_activities"
  | "signature_stories";

const SECTION_NAMES: ReadonlyArray<ProfileSectionKey> = [
  "personal_info",
  "professional_summary",
  "work_experience",
  "education",
  "skills",
  "languages",
  "certifications",
  "projects",
  "publications",
  "volunteer_activities",
  "signature_stories",
];

export function sectionForFieldRef(fieldRef: string | null | undefined): ProfileSectionKey {
  const ref = (fieldRef ?? "").toLowerCase();
  if (!ref) return "skills";

  // 1. An exact section name (first segment of a dotted path, or any item of
  //    a comma-joined list) is authoritative.
  const tokens = ref
    .split(",")
    .map((part) => part.trim().split(".")[0])
    .filter(Boolean);
  for (const token of tokens) {
    const exact = SECTION_NAMES.find((name) => name === token);
    if (exact) return exact;
  }

  // 2. Substring guesses for bare field names.
  if (ref.includes("project")) return "projects";
  if (ref.includes("public") || ref.includes("patent")) return "publications";
  if (ref.includes("volunteer")) return "volunteer_activities";
  if (ref.includes("skill")) return "skills";
  if (ref.includes("summary")) return "professional_summary";
  if (ref.includes("cert")) return "certifications";
  if (ref.includes("educat") || ref.includes("degree") || ref.includes("institution")) return "education";
  if (ref.includes("language") || ref.includes("lang")) return "languages";
  if (
    ref.includes("work") ||
    ref.includes("experience") ||
    ref.includes("role") ||
    ref.includes("title") ||
    ref.includes("company") ||
    ref.includes("date") ||
    ref.includes("budget") ||
    ref.includes("team")
  )
    return "work_experience";
  return "skills";
}
