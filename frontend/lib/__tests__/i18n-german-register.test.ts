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

import { describe, expect, it } from "vitest";
import de from "@/messages/de.json";
import en from "@/messages/en.json";

/**
 * Guards the two i18n defects the key-parity check structurally cannot see
 * (issue #311): a *value* that is still English, and a *register* mismatch
 * between two correctly-present keys.
 */

function flattenValues(obj: unknown, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  if (obj === null || typeof obj !== "object") return out;
  for (const [k, v] of Object.entries(obj)) {
    const next = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      Object.assign(out, flattenValues(v, next));
    } else if (typeof v === "string") {
      out[next] = v;
    }
  }
  return out;
}

const deValues = flattenValues(de);
const enValues = flattenValues(en);

/**
 * BRAND.md §2.3: "Du, not Sie" — consistent across product UI. The only
 * documented exceptions are legal pages (Impressum, Datenschutzerklärung) and
 * B2B sales material; neither is served from these catalogs.
 *
 * Matches the capitalised formal pronouns only. Lowercase "sie/ihr" (third
 * person) is untouched; a sentence-initial third-person "Sie" would be a false
 * positive — rephrase rather than weaken this guard.
 */
const FORMAL_PRONOUNS = /\b(Sie|Ihnen|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres)\b/;

/**
 * Keys whose German value is legitimately identical to the English one:
 * brand names, loanwords that are the normal German term, bare symbols, and
 * ICU patterns that carry no prose of their own. Anything not on this list
 * that matches its English counterpart is an untranslated string.
 */
const IDENTICAL_VALUE_ALLOWLIST = new Set([
  // Brand / product names
  "shell.appName",
  "shell.appLogoAlt",
  "quickTailor.title",
  // Loanwords that are the ordinary German term
  "nav.dashboard",
  "shell.dashboard",
  "nav.admin",
  "cv.designTab",
  "coverLetter.designTab",
  "cv.statusHeaderMatchLabel",
  "documents.colStatus",
  "applications.details",
  "ats.detailsButton",
  "truthfulness.detailsButton",
  "profile.sources.interview",
  "flow.stepInterview",
  "profile.fieldName",
  "profile.entryEditor.currentStatusLabel",
  "profile.educationEditor.fieldInstitution",
  "profile.skillsEditor.fieldName",
  "profile.certificationsEditor.fieldName",
  "profile.projectsEditor.fieldName",
  "profile.projectsEditor.fieldUrl",
  "profile.publicationsEditor.fieldDoi",
  "profile.publicationsEditor.fieldUrl",
  "profile.publicationsEditor.typePatent",
  "profile.fieldLinkedin",
  "profile.fieldXing",
  "profile.personalInfoEditor.fieldName",
  "profile.personalInfoEditor.fieldLinkedin",
  "profile.personalInfoEditor.fieldXing",
  "coverLetter.recipientNamePlaceholder",
  "home.jdUrlTab",
  "dashboard.jdTabUrl",
  "coverLetter.autoTag",
  "coverLetter.optionalHint",
  // #626 Health-hub field labels — same two words already allowlisted for the
  // structured editors above (profile.educationEditor.fieldInstitution,
  // profile.projectsEditor.fieldUrl): identical in both languages.
  "health.fieldLabel.institution",
  "health.fieldLabel.url",
  "health.fieldLabel.position",
  "health.fieldLabel.name",
  // Symbols, separators and pure ICU patterns
  "coverLetter.separator",
  "coverLetter.emDash",
  "coverLetter.breadcrumbSeparator",
  "shell.topbarSeparator",
  "shell.topbarUserInitial",
  "shell.backArrowLabel",
  "admin.schemeEditor.iconNewScheme",
  "match.emptyIcon",
  "match.strengthPrefix",
  "match.gapPrefix",
  "gaps.matchScoreDisplay",
  "interview.matchScorePercent",
  "cv.matchScorePercent",
  "cv.completenessPercent",
  "cv.profileChangeLog",
  "profile.changeLog",
  "dashboard.filterStatusChip",
  "review.storyLabel",
  "profileUpdate.addRole.requiredSuffix",
  // Literal confirmation token — must stay typeable as shown
  "settings.deleteToken",
  // URL example
  "quickTailor.urlPlaceholder",
  // #626 conflict headings — pure ICU placeholder patterns, no prose to translate
  "health.conflictHeadingWithEntity",
  "health.conflictHeadingGeneral",
]);

describe("German UI register (BRAND.md §2.3)", () => {
  it("de.json addresses the user as 'du', never 'Sie'", () => {
    const offenders = Object.entries(deValues)
      .filter(([, value]) => FORMAL_PRONOUNS.test(value))
      .map(([key, value]) => `${key}: ${value}`);
    expect(offenders).toEqual([]);
  });
});

describe("German catalog completeness", () => {
  it("no de.json value is left as its untranslated English counterpart", () => {
    const offenders = Object.entries(deValues)
      .filter(([key, value]) => enValues[key] === value)
      .filter(([key]) => !IDENTICAL_VALUE_ALLOWLIST.has(key))
      .map(([key, value]) => `${key}: ${value}`);
    expect(offenders).toEqual([]);
  });

  it("the identical-value allowlist has no stale entries", () => {
    const stale = [...IDENTICAL_VALUE_ALLOWLIST].filter(
      (key) => deValues[key] === undefined || deValues[key] !== enValues[key]
    );
    expect(stale).toEqual([]);
  });
});
