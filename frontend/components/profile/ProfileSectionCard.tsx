"use client";

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

// F8 (#76) — readable profile sections. Renders the Master Profile as formatted
// cards instead of raw JSON, hiding internal plumbing (id, source, *_refs,
// role_aliases) from the DACH/non-engineer persona. F9.2 — the summary is a
// {de, en} pair; chrome follows ui_language, generated content follows the JD
// (ADR-038), so we surface whichever localized summary actually exists.

import { useTranslations } from "next-intl";

export type UiLanguage = "de" | "en";

const PROFICIENCY_KEYS = new Set(["basic", "intermediate", "advanced", "expert"]);

export type LocalizedText = { de?: string | null; en?: string | null };

/** A summary is either a localized {de,en} pair or a legacy plain string. */
export type SummaryValue = LocalizedText | string | null | undefined;

export interface ResolvedSummary {
  /** The best available summary text, or null if none exists in any language. */
  text: string | null;
  /** True only when NO language has a summary (genuinely incomplete). */
  missing: boolean;
  /** When a summary exists but the UI language's variant is absent, which one. */
  missingLanguage?: UiLanguage;
}

/**
 * Resolve which summary to show. Prefers the UI language; if absent, falls back
 * to the other language and notes which one is missing (so the dashboard can
 * explain "German summary missing" instead of silently flagging the whole
 * section incomplete — F9.2).
 */
export function resolveSummary(
  value: SummaryValue,
  uiLanguage: UiLanguage,
): ResolvedSummary {
  if (typeof value === "string") {
    const t = value.trim();
    return t ? { text: t, missing: false } : { text: null, missing: true };
  }
  if (!value) return { text: null, missing: true };

  const other: UiLanguage = uiLanguage === "de" ? "en" : "de";
  const primary = (value[uiLanguage] ?? "").trim();
  const secondary = (value[other] ?? "").trim();

  if (primary) return { text: primary, missing: false };
  if (secondary) {
    return { text: secondary, missing: false, missingLanguage: uiLanguage };
  }
  return { text: null, missing: true };
}

export type SectionKey =
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

interface WorkEntryShape {
  role?: string | null;
  title?: string | null;
  company?: string | null;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  // #155 — tri-state current-position marker (true = ongoing, false = ended,
  // null/undefined = unknown)
  is_current?: boolean | null;
  responsibilities?: string[] | null;
  achievements?: string[] | null;
  technologies?: string[] | null;
  description?: string | null;
}

interface EducationShape {
  degree?: string | null;
  field?: string | null;
  institution?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  year?: string | null;
  grade?: string | null;
}

interface SkillShape {
  name?: string | null;
  proficiency?: string | null;
}

// #113(c) — the vault's canonical field is `language`, not `name`: backend
// `Language` (schemas/profile.py), `TailoredLanguage` (schemas/cv.py) and every
// CV template (`lang.language`) agree. Reading `name` made a profile carrying
// German (Native) / English (C1) render as "Not provided". `name` stays as a
// tolerated legacy alias so no hand-edited record goes invisible either.
interface LanguageShape {
  language?: string | null;
  name?: string | null;
  level?: string | null;
  proficiency?: string | null;
}

interface CertificationShape {
  name?: string | null;
  issuing_organization?: string | null;
  issuer?: string | null;
  date_obtained?: string | null;
  year?: string | null;
}

// US292 — ProjectEntry/VolunteerActivity extend the backend's ExperienceBase
// (ADR-044), same span/bullets shape as WorkEntryShape above, plus their own
// natural-key fields.
interface ProjectShape {
  name?: string | null;
  role?: string | null;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  is_current?: boolean | null;
  responsibilities?: string[] | null;
  achievements?: string[] | null;
  technologies?: string[] | null;
  description?: string | null;
}

interface PublicationShape {
  title?: string | null;
  type?: "publication" | "patent" | null;
  venue?: string | null;
  published_date?: string | null;
}

interface VolunteerShape {
  organization?: string | null;
  role?: string | null;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  is_current?: boolean | null;
  responsibilities?: string[] | null;
  achievements?: string[] | null;
  technologies?: string[] | null;
  description?: string | null;
  cause?: string | null;
}

interface SignatureStoryShape {
  id?: string;
  title?: string | null;
  challenge?: string | null;
  mechanism?: string | null;
  outcome?: string | null;
  benchmark?: string | null;
  experience_refs?: string[];
  source?: string | null;
}

interface PersonalInfoShape {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  location?: string | null;
}

function nonEmpty(s?: string | null): s is string {
  return typeof s === "string" && s.trim().length > 0;
}

function formatPeriod(
  start?: string | null,
  end?: string | null,
  presentLabel?: string,
  isCurrent?: boolean | null,
): string | null {
  if (!nonEmpty(start) && !nonEmpty(end)) return null;
  const left = nonEmpty(start) ? start : null;
  const explicitEnd = nonEmpty(end) ? end : null;
  // #155 — an explicit is_current === true always renders the present label;
  // an empty end keeps the existing present-label fallback.
  const right =
    isCurrent === true
      ? (presentLabel ?? explicitEnd)
      : (explicitEnd ?? presentLabel ?? null);
  // #113(d) — a missing side is rendered by omission, never by an em-dash
  // standing in for data we do not have ("— → 2006"). Mirrors the DACH
  // convention already shipped in the CV templates (lebenslauf.html.j2: an
  // education entry without a start date renders the end date alone, no dash).
  if (!left) return right;
  if (!right) return left;
  return `${left} → ${right}`;
}

export function ProfileSectionBody({
  section,
  value,
  uiLanguage,
}: {
  section: SectionKey;
  value: unknown;
  uiLanguage: UiLanguage;
}) {
  const t = useTranslations("profile");

  const empty = (
    <p className="text-gray-400 italic text-sm">{t("notProvided")}</p>
  );

  if (section === "professional_summary") {
    const resolved = resolveSummary(value as SummaryValue, uiLanguage);
    if (!resolved.text) return empty;
    return (
      <div className="space-y-2">
        <p className="text-sm text-gray-700 leading-relaxed">{resolved.text}</p>
        {resolved.missingLanguage && (
          <p className="text-xs text-amber-600">
            {t("summaryLanguageMissing", {
              lang: t(`language_${resolved.missingLanguage}`),
            })}
          </p>
        )}
      </div>
    );
  }

  if (section === "personal_info") {
    const info = (value ?? {}) as PersonalInfoShape;
    const rows: Array<[string, string | null | undefined]> = [
      [t("fieldName"), info.name],
      [t("fieldEmail"), info.email],
      [t("fieldPhone"), info.phone],
      [t("fieldLocation"), info.location],
    ];
    const shown = rows.filter(([, v]) => nonEmpty(v));
    if (shown.length === 0) return empty;
    return (
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        {shown.map(([label, v]) => (
          <div key={label} className="contents">
            <dt className="text-gray-500">{label}</dt>
            <dd className="text-gray-800">{v}</dd>
          </div>
        ))}
      </dl>
    );
  }

  if (section === "work_experience") {
    const entries = Array.isArray(value) ? (value as WorkEntryShape[]) : [];
    if (entries.length === 0) return empty;
    return (
      <div className="space-y-4">
        {entries.map((e, i) => {
          const role = e.role || e.title || "";
          const period = formatPeriod(
            e.start_date,
            e.end_date,
            t("present"),
            e.is_current,
          );
          const bullets = [
            ...(e.achievements ?? []),
            ...(e.responsibilities ?? []),
          ].filter(nonEmpty);
          return (
            <div key={i} className="border-l-2 border-teal/40 pl-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <p className="text-sm font-semibold text-neutral-dark">
                  {role || e.company || t("notProvided")}
                </p>
                {period && (
                  <span className="text-xs text-gray-500">{period}</span>
                )}
              </div>
              {nonEmpty(e.company) && (role || "") !== e.company && (
                <p className="text-xs text-gray-600">
                  {[e.company, nonEmpty(e.location) ? e.location : null]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              )}
              {nonEmpty(e.description) && (
                <p className="mt-1 text-sm text-gray-700">{e.description}</p>
              )}
              {bullets.length > 0 && (
                <ul className="mt-1.5 list-disc pl-4 space-y-0.5 text-sm text-gray-700">
                  {bullets.map((b, bi) => (
                    <li key={bi} className="break-words">{b}</li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (section === "education") {
    const entries = Array.isArray(value) ? (value as EducationShape[]) : [];
    if (entries.length === 0) return empty;
    return (
      <div className="space-y-3">
        {entries.map((e, i) => {
          const heading = [e.degree, e.field].filter(nonEmpty).join(", ");
          // #113(d) — education gets the same present label as work: an
          // ongoing degree reads "2023-09 → present", not a dangling dash.
          const period =
            formatPeriod(e.start_date, e.end_date, t("present")) ??
            (nonEmpty(e.year) ? e.year : null);
          return (
            <div key={i} className="border-l-2 border-teal/40 pl-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <p className="text-sm font-semibold text-neutral-dark">
                  {heading || e.institution || t("notProvided")}
                </p>
                {period && (
                  <span className="text-xs text-gray-500">{period}</span>
                )}
              </div>
              {nonEmpty(e.institution) && heading !== "" && (
                <p className="text-xs text-gray-600">{e.institution}</p>
              )}
              {nonEmpty(e.grade) && (
                <p className="text-xs text-gray-500">{e.grade}</p>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (section === "skills") {
    const entries = Array.isArray(value) ? (value as SkillShape[]) : [];
    const named = entries.filter((s) => nonEmpty(s.name));
    if (named.length === 0) return empty;
    return (
      <div className="flex flex-wrap gap-2">
        {named.map((s, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1.5 rounded-full bg-surface-container px-3 py-1 text-xs text-neutral-dark"
          >
            <span className="font-medium">{s.name}</span>
            {nonEmpty(s.proficiency) && (
              <span className="text-gray-500">
                {PROFICIENCY_KEYS.has(s.proficiency)
                  ? t(`proficiency_${s.proficiency}` as "proficiency_basic")
                  : s.proficiency}
              </span>
            )}
          </span>
        ))}
      </div>
    );
  }

  if (section === "languages") {
    const entries = Array.isArray(value) ? (value as LanguageShape[]) : [];
    const named = entries.filter((l) => nonEmpty(l.language) || nonEmpty(l.name));
    if (named.length === 0) return empty;
    return (
      <div className="flex flex-wrap gap-2">
        {named.map((l, i) => {
          const level = l.level ?? l.proficiency;
          const name = nonEmpty(l.language) ? l.language : l.name;
          return (
            <span
              key={i}
              className="inline-flex items-center gap-1.5 rounded-full bg-surface-container px-3 py-1 text-xs text-neutral-dark"
            >
              <span className="font-medium">{name}</span>
              {nonEmpty(level) && <span className="text-gray-500">{level}</span>}
            </span>
          );
        })}
      </div>
    );
  }

  if (section === "projects") {
    // Mirrors the work_experience branch above — same ExperienceBase shape
    // (period, description, bullets), plus technologies rendered as chips
    // (US292).
    const entries = Array.isArray(value) ? (value as ProjectShape[]) : [];
    const named = entries.filter((p) => nonEmpty(p.name));
    if (named.length === 0) return empty;
    return (
      <div className="space-y-4">
        {named.map((p, i) => {
          const period = formatPeriod(p.start_date, p.end_date, t("present"), p.is_current);
          const bullets = [...(p.achievements ?? []), ...(p.responsibilities ?? [])].filter(nonEmpty);
          const technologies = (p.technologies ?? []).filter(nonEmpty);
          return (
            <div key={i} className="border-l-2 border-teal/40 pl-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <p className="text-sm font-semibold text-neutral-dark">{p.name}</p>
                {period && <span className="text-xs text-gray-500">{period}</span>}
              </div>
              {nonEmpty(p.role) && <p className="text-xs text-gray-600">{p.role}</p>}
              {nonEmpty(p.description) && (
                <p className="mt-1 text-sm text-gray-700">{p.description}</p>
              )}
              {bullets.length > 0 && (
                <ul className="mt-1.5 list-disc pl-4 space-y-0.5 text-sm text-gray-700">
                  {bullets.map((b, bi) => (
                    <li key={bi} className="break-words">{b}</li>
                  ))}
                </ul>
              )}
              {technologies.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {technologies.map((tech, ti) => (
                    <span
                      key={ti}
                      className="inline-flex items-center rounded-full bg-surface-container px-2 py-0.5 text-xs text-neutral-dark"
                    >
                      {tech}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (section === "publications") {
    const entries = Array.isArray(value) ? (value as PublicationShape[]) : [];
    const named = entries.filter((p) => nonEmpty(p.title));
    if (named.length === 0) return empty;
    return (
      <ul className="space-y-2 text-sm">
        {named.map((p, i) => (
          <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-2">
            <span className="flex items-center gap-1.5">
              <span className="font-medium text-neutral-dark">{p.title}</span>
              {p.type === "patent" && (
                <span className="inline-flex items-center rounded-full bg-surface-container px-2 py-0.5 text-[11px] font-medium text-on-surface-variant">
                  {t("publicationsEditor.typePatent")}
                </span>
              )}
            </span>
            <span className="text-xs text-gray-500">
              {[p.venue, p.published_date].filter(nonEmpty).join(" · ")}
            </span>
          </li>
        ))}
      </ul>
    );
  }

  if (section === "volunteer_activities") {
    const entries = Array.isArray(value) ? (value as VolunteerShape[]) : [];
    const named = entries.filter((v) => nonEmpty(v.organization) || nonEmpty(v.role));
    if (named.length === 0) return empty;
    return (
      <div className="space-y-4">
        {named.map((v, i) => {
          const heading = [v.role, v.organization].filter(nonEmpty).join(" @ ");
          const period = formatPeriod(v.start_date, v.end_date, t("present"), v.is_current);
          const bullets = [...(v.achievements ?? []), ...(v.responsibilities ?? [])].filter(nonEmpty);
          return (
            <div key={i} className="border-l-2 border-teal/40 pl-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <p className="text-sm font-semibold text-neutral-dark">{heading || t("notProvided")}</p>
                {period && <span className="text-xs text-gray-500">{period}</span>}
              </div>
              {nonEmpty(v.cause) && <p className="text-xs text-gray-600">{v.cause}</p>}
              {nonEmpty(v.description) && (
                <p className="mt-1 text-sm text-gray-700">{v.description}</p>
              )}
              {bullets.length > 0 && (
                <ul className="mt-1.5 list-disc pl-4 space-y-0.5 text-sm text-gray-700">
                  {bullets.map((b, bi) => (
                    <li key={bi} className="break-words">{b}</li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (section === "signature_stories") {
    // ADR-055 (E046) — read-only narrative cards; internal ids/source hidden
    // (F8 rule). The page hides the whole section when empty; this empty state
    // only covers a direct render with no usable entries.
    const stories = Array.isArray(value) ? (value as SignatureStoryShape[]) : [];
    const titled = stories.filter((s) => nonEmpty(s.title));
    if (titled.length === 0) return empty;
    const fieldRows = (s: SignatureStoryShape): Array<[string, string]> =>
      (
        [
          [t("storyChallenge"), s.challenge],
          [t("storyMechanism"), s.mechanism],
          [t("storyOutcome"), s.outcome],
          [t("storyBenchmark"), s.benchmark],
        ] as Array<[string, string | null | undefined]>
      ).filter((row): row is [string, string] => nonEmpty(row[1]));
    return (
      <ul className="space-y-4">
        {titled.map((s, i) => (
          <li key={s.id ?? i} className="rounded-lg bg-surface-container p-3">
            <p className="font-medium text-neutral-dark text-sm mb-1.5">{s.title}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              {fieldRows(s).map(([label, text]) => (
                <div key={label} className="contents">
                  <dt className="text-gray-500">{label}</dt>
                  <dd className="text-gray-700">{text}</dd>
                </div>
              ))}
            </dl>
            {(s.experience_refs?.length ?? 0) > 0 && (
              <p className="mt-1.5 text-xs text-gray-500">
                {t("storyLinkedExperiences", { count: s.experience_refs!.length })}
              </p>
            )}
          </li>
        ))}
      </ul>
    );
  }

  // certifications
  const entries = Array.isArray(value) ? (value as CertificationShape[]) : [];
  const named = entries.filter((c) => nonEmpty(c.name));
  if (named.length === 0) return empty;
  return (
    <ul className="space-y-2 text-sm">
      {named.map((c, i) => {
        const issuer = c.issuing_organization ?? c.issuer;
        const when = c.date_obtained ?? c.year;
        return (
          <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-2">
            <span className="font-medium text-neutral-dark">{c.name}</span>
            <span className="text-xs text-gray-500">
              {[issuer, when].filter(nonEmpty).join(" · ")}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
