# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""
Merge logic for combining new profile data with existing.

Core design principle (per ADR / Carla's architecture note):
  Most "differences" between CVs are ACCUMULATION, not CONFLICT.
  The same job can surface as "Team Lead" in one CV and "2nd Level Support"
  in another — both are true simultaneously (different perspectives for
  different applications). The Master Profile stores ALL facets.

A true conflict is only a factual irreconcilability — something where only
one value can be correct at a time (e.g. contradicting start dates).

Rules:
- NEVER silently overwrite existing data.
- Accumulate lists (responsibilities, achievements, technologies, role_aliases).
- Only flag true factual contradictions as Conflict records.
- Non-conflicting gap-fills are applied automatically.
"""
from dataclasses import dataclass, field

from applire.schemas.profile import (
    Conflict,
    FieldChange,
    MasterProfileData,
    ProjectEntry,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconciliation import compute_merge_reconciliation

# Personal-info fields that are user-managed and must never be flagged as
# LLM-import conflicts. Gap-fill (empty → value) is still applied normally.
_GAP_FILL_ONLY: frozenset[str] = frozenset({"photo_url"})


@dataclass
class MergeResult:
    merged_profile: MasterProfileData
    added: list[str] = field(default_factory=list)   # descriptions of auto-added / enriched items
    conflicts: list[Conflict] = field(default_factory=list)
    # ADR-040 / US145: structured, per-decision change records (one FieldChange per
    # auto-decision) so the "what changed & why" surfaces render from data, not from
    # parsing the human-readable `added` strings. `added` is retained for back-compat.
    changes: list[FieldChange] = field(default_factory=list)
    # US161 (ADR-041 amended) — per-entity {extracted, stored, delta}; observational.
    reconciliation: dict[str, dict[str, int]] = field(default_factory=dict)


def _dates_overlap(
    a_start: str | None,
    a_end: str | None,
    b_start: str | None,
    b_end: str | None,
) -> bool:
    """
    Best-effort overlap check on partial date strings like '2020-01' or '2020'.
    Returns True when dates cannot be determined (safe default — treats as overlap).

    Uses strict inequality so that adjacent roles at the same employer (one ends
    in 2021-05, the next starts in 2021-05) are treated as separate positions,
    not merged. Two ranges overlap only if one START is strictly before the other
    END — a shared boundary month is a job transition, not concurrent employment.
    """
    if not a_start or not b_start:
        return True
    a_s = (a_start + "-01")[:7]
    a_e = (a_end + "-12")[:7] if a_end else "9999-12"
    b_s = (b_start + "-01")[:7]
    b_e = (b_end + "-12")[:7] if b_end else "9999-12"
    return a_s < b_e and b_s < a_e


def _dates_contradict(a: str | None, b: str | None) -> bool:
    """
    Returns True if two date strings are both present and clearly different.
    Uses YYYY-MM prefix comparison — partial dates like '2020' vs '2020-01'
    are NOT considered contradictions (one is just more precise).
    """
    if not a or not b:
        return False
    a_norm = (a + "-01")[:7]
    b_norm = (b + "-01")[:7]
    return a_norm != b_norm


def _merge_str_lists(a: list[str], b: list[str]) -> list[str]:
    """Union of two string lists, deduplicating by case-insensitive equality."""
    seen = {v.lower() for v in a}
    result = list(a)
    for v in b:
        if v.lower() not in seen:
            result.append(v)
            seen.add(v.lower())
    return result


_LEGAL_SUFFIXES = frozenset({
    "gmbh", "se", "ag", "ggmbh", "kg", "kgaa", "ohg", "gbr",
    "inc", "ltd", "llc", "corp", "plc", "bv", "nv", "sa",
})
_STOPWORDS = frozenset({
    "des", "der", "die", "das", "und", "von", "am", "im", "an",
    "the", "of", "and", "&", "co",
})


def _company_tokens(name: str) -> frozenset[str]:
    """Return significant tokens from a company name.

    Strips common legal-form suffixes and stopwords, then returns tokens
    with more than 3 characters so noise like 'Co', 'BRK', 'SE' don't drive
    false matches.
    """
    tokens: list[str] = []
    for raw in name.lower().split():
        tok = raw.strip(".,-()")
        if tok in _LEGAL_SUFFIXES or tok in _STOPWORDS:
            continue
        if len(tok) > 3:
            tokens.append(tok)
    return frozenset(tokens)


def _company_names_match(a: str, b: str) -> bool:
    """Return True when a and b likely refer to the same employer.

    Exact match (fast path) or subset match: one name's significant-token
    set is a subset of the other's, indicating one is a shortened form of
    the same organisation (e.g. 'Roche' ⊆ 'Roche Diagnostics GmbH').
    """
    a_l = a.strip().lower()
    b_l = b.strip().lower()
    if a_l == b_l:
        return True
    ta = _company_tokens(a)
    tb = _company_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def _dates_determinately_overlap(
    a_start: str | None,
    a_end: str | None,
    b_start: str | None,
    b_end: str | None,
) -> bool:
    """Positive, evidence-based overlap: both ranges have a start date AND actually
    overlap. Unlike `_dates_overlap`, a missing date is NOT treated as overlap.

    Used to gate fusion on *loose* (subset) company matches (JF-M-3.4 chimera guard):
    a short-form/long-form company match is weak evidence, so it must be corroborated
    by a real date overlap before two entries are merged into one.
    """
    if not a_start or not b_start:
        return False
    return _dates_overlap(a_start, a_end, b_start, b_end)


def _company_names_match_exact(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


# Public aliases — shared with the interview enrichment path
# (services/interview_graph.profile_updater), which must apply the same
# employer-identity and date-overlap semantics as the CV-upload merge.
company_names_match = _company_names_match
dates_overlap = _dates_overlap


def _sort_work_by_date(entries: list[WorkEntry]) -> list[WorkEntry]:
    """Sort work entries reverse-chronologically (most recent first).

    Entries with no end_date (ongoing roles) sort first (treated as 9999-12).
    """
    def _key(e: WorkEntry) -> str:
        if not e.end_date:
            return "9999-12"
        return (e.end_date + "-12")[:7]

    return sorted(entries, key=_key, reverse=True)


def _merge_work_experience(
    existing: list[WorkEntry],
    incoming: list[WorkEntry],
    source: str,
) -> tuple[list[WorkEntry], list[str], list[Conflict], list[FieldChange]]:
    """
    Same company + overlapping dates = same position with different facets.

    Accumulation rules (no conflict, just enrichment):
    - responsibilities, achievements, technologies: union, deduplicated
    - role_aliases: collect all titles ever used for this position
    - industry_context, team_size, budget_managed: fill gaps; keep existing if both present

    True conflict rules (flag for user resolution):
    - start_date: both present and clearly different YYYY-MM
    - end_date: both present and clearly different YYYY-MM

    Different company OR non-overlapping dates → append as new entry.
    """
    result = list(existing)
    added: list[str] = []
    conflicts: list[Conflict] = []
    changes: list[FieldChange] = []

    for inc in incoming:
        # Drop shell entries: no company, or no dates AND no content — these are
        # misclassified role aliases that the LLM should have placed in role_aliases.
        if not inc.company.strip():
            continue
        if (
            not inc.start_date
            and not inc.end_date
            and not inc.responsibilities
            and not inc.achievements
        ):
            # Attempt to attach the title as a role_alias on a same-company entry
            for idx, ex in enumerate(result):
                if _company_names_match(ex.company, inc.company):
                    new_aliases = list(result[idx].role_aliases)
                    for title in [inc.role] + inc.role_aliases:
                        if title.strip().lower() not in {t.lower() for t in new_aliases + [result[idx].role]}:
                            new_aliases.append(title)
                    result[idx] = result[idx].model_copy(update={"role_aliases": new_aliases})
                    added.append(f"role_alias '{inc.role}' → {inc.company}")
                    changes.append(FieldChange(
                        section="work_experience", field="role_aliases", action="merged",
                        new_value=f"{inc.role} @ {inc.company}",
                        rationale=f"'{inc.role}' looked like a job title within your {inc.company} "
                                  f"role, so we added it as an alias rather than a separate position.",
                        rationale_key="role_alias_added",
                    ))
                    break
            continue

        matched = False
        for idx, ex in enumerate(result):
            if not _company_names_match(ex.company, inc.company):
                continue
            # Chimera guard (JF-M-3.4): a *loose* subset company match ("Apple" ⊆
            # "Apple Bank") is weak evidence of the same employer. Require a real,
            # determinate date overlap before fusing. An *exact* company name is
            # strong enough to merge even with fuzzy/missing dates.
            if not _company_names_match_exact(ex.company, inc.company):
                if not _dates_determinately_overlap(
                    ex.start_date, ex.end_date, inc.start_date, inc.end_date
                ):
                    continue
            if not _dates_overlap(ex.start_date, ex.end_date, inc.start_date, inc.end_date):
                continue

            # Same company + overlapping dates → same position, different facets
            matched = True
            merged = ex.model_copy(deep=True)

            # Accumulate all role titles (not a conflict — different CVs surface different roles)
            new_aliases = list(merged.role_aliases)
            for title in [inc.role] + inc.role_aliases:
                if title.strip().lower() not in {t.lower() for t in new_aliases + [merged.role]}:
                    new_aliases.append(title)
            merged = merged.model_copy(update={"role_aliases": new_aliases})

            # Accumulate list fields
            merged = merged.model_copy(update={
                "responsibilities": _merge_str_lists(merged.responsibilities, inc.responsibilities),
                "achievements": _merge_str_lists(merged.achievements, inc.achievements),
                "technologies": _merge_str_lists(merged.technologies, inc.technologies),
            })

            # Fill gap fields (only when existing is empty)
            if not merged.industry_context and inc.industry_context:
                merged = merged.model_copy(update={"industry_context": inc.industry_context})
                added.append(f"work_experience[{ex.company}].industry_context")
            if merged.team_size is None and inc.team_size is not None:
                merged = merged.model_copy(update={"team_size": inc.team_size})
                added.append(f"work_experience[{ex.company}].team_size")
            if not merged.budget_managed and inc.budget_managed:
                merged = merged.model_copy(update={"budget_managed": inc.budget_managed})
                added.append(f"work_experience[{ex.company}].budget_managed")

            # Detect true factual contradictions (dates only)
            if _dates_contradict(ex.start_date, inc.start_date):
                conflicts.append(Conflict(
                    section="work_experience",
                    field="start_date",
                    existing_value=ex.start_date,
                    incoming_value=inc.start_date,
                    source=source,
                    suggested_resolution=f"Verify start date for '{ex.company}'",
                ))
            if _dates_contradict(ex.end_date, inc.end_date):
                conflicts.append(Conflict(
                    section="work_experience",
                    field="end_date",
                    existing_value=ex.end_date,
                    incoming_value=inc.end_date,
                    source=source,
                    suggested_resolution=f"Verify end date for '{ex.company}'",
                ))

            # Record the (otherwise silent) accumulation so the user can see it and
            # split it if our same-position assumption was wrong (JF-M-3.2 / 3.4).
            exact = _company_names_match_exact(ex.company, inc.company)
            changes.append(FieldChange(
                section="work_experience", field="work_experience", action="merged",
                old_value=f"{ex.role} @ {ex.company}",
                new_value=f"{inc.role} @ {inc.company}",
                rationale=(
                    f"Treated as the same position at {ex.company} "
                    f"({'same employer name' if exact else 'matching employer name'} + overlapping dates) "
                    f"and combined the titles and details. Not the same job? You can split it."
                ),
                rationale_key="merged_same_position",
            ))

            result[idx] = merged
            break

        if not matched:
            result.append(inc)
            added.append(f"work_experience: {inc.role} at {inc.company}")
            changes.append(FieldChange(
                section="work_experience", field="work_experience", action="added",
                new_value=f"{inc.role} at {inc.company}",
                rationale="New position added from this source.",
                rationale_key="new_position",
            ))

    return _sort_work_by_date(result), added, conflicts, changes


def _merge_skills(
    existing: list[Skill],
    incoming: list[Skill],
    source: str,
) -> tuple[list[Skill], list[str], list[Conflict]]:
    """
    Rules:
    1. Same skill name → keep higher proficiency + higher years_experience (accumulation)
    2. New skill → add
    No conflicts — proficiency differences are always resolved by taking the higher value.
    """
    _PROF_RANK = {"basic": 0, "intermediate": 1, "advanced": 2, "expert": 3}
    result = list(existing)
    added: list[str] = []

    for inc in incoming:
        match = next(
            (s for s in result if s.name.strip().lower() == inc.name.strip().lower()),
            None,
        )
        if match is None:
            result.append(inc)
            added.append(f"skill: {inc.name}")
        else:
            idx = result.index(match)
            best_prof = (
                match.proficiency
                if _PROF_RANK[match.proficiency] >= _PROF_RANK[inc.proficiency]
                else inc.proficiency
            )
            best_years: int | None = match.years_experience
            if inc.years_experience is not None:
                if best_years is None or inc.years_experience > best_years:
                    best_years = inc.years_experience

            result[idx] = match.model_copy(update={
                "proficiency": best_prof,
                "years_experience": best_years,
                "source": source,
            })

    return result, added, []


def _merge_projects(
    existing: list[ProjectEntry],
    incoming: list[ProjectEntry],
) -> list[ProjectEntry]:
    """Additive merge for projects (US172 / ADR-013 accumulation-first).

    Identity/dedup key: project name (trimmed, casefolded).
    - New name → append.
    - Matching name → accumulate responsibilities/achievements/technologies
      (union via _merge_str_lists, preserve order, no duplicates); fill
      empty scalar fields (description, url, start_date, end_date,
      associated_experience, role) from incoming ONLY when the existing
      value is empty/None. Never overwrite a non-empty existing value.
      Keep the existing id.
    """
    result: list[ProjectEntry] = list(existing)

    for inc in incoming:
        # Normalised name for identity matching
        inc_key = inc.name.strip().casefold()

        match_idx: int | None = None
        for idx, ex in enumerate(result):
            if ex.name.strip().casefold() == inc_key:
                match_idx = idx
                break

        if match_idx is None:
            # New project — append as-is
            result.append(inc)
        else:
            # Matching project — accumulate lists; gap-fill empty scalars; keep id
            ex = result[match_idx]
            updates: dict = {
                "responsibilities": _merge_str_lists(ex.responsibilities, inc.responsibilities),
                "achievements": _merge_str_lists(ex.achievements, inc.achievements),
                "technologies": _merge_str_lists(ex.technologies, inc.technologies),
            }

            # Gap-fill: only fill when existing is empty/None
            for attr in ("description", "url", "start_date", "end_date",
                         "associated_experience", "role"):
                ex_val = getattr(ex, attr)
                inc_val = getattr(inc, attr)
                # For role, empty string ("") is also considered empty
                if not ex_val and inc_val:
                    updates[attr] = inc_val

            result[match_idx] = ex.model_copy(update=updates)

    return result


def merge_profiles(
    existing: MasterProfileData,
    incoming: MasterProfileData,
    source: str,
) -> MergeResult:
    """
    Merge *incoming* profile data into *existing*.
    Returns a MergeResult with the merged profile and any flagged conflicts.
    """
    all_added: list[str] = []
    all_conflicts: list[Conflict] = []
    all_changes: list[FieldChange] = []

    # Work experience
    merged_work, work_added, work_conflicts, work_changes = _merge_work_experience(
        existing.work_experience, incoming.work_experience, source
    )
    all_added.extend(work_added)
    all_conflicts.extend(work_conflicts)
    all_changes.extend(work_changes)

    # Skills
    merged_skills, skills_added, _ = _merge_skills(
        existing.skills, incoming.skills, source
    )
    all_added.extend(skills_added)
    for _s in skills_added:
        all_changes.append(FieldChange(
            section="skills", field="skills", action="added",
            new_value=_s, rationale="New skill from this source.",
            rationale_key="new_skill",
        ))

    # Education — append non-duplicate entries (match on institution + degree)
    merged_edu = list(existing.education)
    for inc_e in incoming.education:
        duplicate = any(
            e.institution.strip().lower() == inc_e.institution.strip().lower()
            and e.degree.strip().lower() == inc_e.degree.strip().lower()
            for e in merged_edu
        )
        if not duplicate:
            merged_edu.append(inc_e)
            all_added.append(f"education: {inc_e.degree} at {inc_e.institution}")
            all_changes.append(FieldChange(
                section="education", field="education", action="added",
                new_value=f"{inc_e.degree} at {inc_e.institution}",
                rationale="New education entry from this source.",
                rationale_key="new_education",
            ))

    # Languages — keep higher/new levels
    merged_langs = list(existing.languages)
    for inc_l in incoming.languages:
        match = next(
            (l for l in merged_langs if l.language.lower() == inc_l.language.lower()),
            None,
        )
        if match is None:
            merged_langs.append(inc_l)
            all_added.append(f"language: {inc_l.language}")
            all_changes.append(FieldChange(
                section="languages", field="languages", action="added",
                new_value=inc_l.language, rationale="New language from this source.",
                rationale_key="new_language",
            ))
        # else keep existing level (user can patch manually)

    # Certifications — append non-duplicate (match on name)
    merged_certs = list(existing.certifications)
    for inc_c in incoming.certifications:
        if not any(c.name.lower() == inc_c.name.lower() for c in merged_certs):
            merged_certs.append(inc_c)
            all_added.append(f"certification: {inc_c.name}")
            all_changes.append(FieldChange(
                section="certifications", field="certifications", action="added",
                new_value=inc_c.name, rationale="New certification from this source.",
                rationale_key="new_certification",
            ))

    # Personal info — fill gaps; flag only populated-vs-different values
    merged_pi = existing.personal_info.model_copy(deep=True)
    inc_pi = incoming.personal_info

    for attr in ("name", "email", "phone", "location", "address",
                 "nationality", "linkedin_url", "xing_url", "website_url",
                 "photo_url"):
        ex_val = getattr(merged_pi, attr)
        inc_val = getattr(inc_pi, attr)
        if inc_val and not ex_val:
            merged_pi = merged_pi.model_copy(update={attr: inc_val})
            all_added.append(f"personal_info.{attr}")
        elif inc_val and ex_val and str(inc_val).strip().lower() != str(ex_val).strip().lower():
            if attr in _GAP_FILL_ONLY:
                continue  # photo_url is user-managed; never auto-conflict
            all_conflicts.append(Conflict(
                section="personal_info",
                field=attr,
                existing_value=ex_val,
                incoming_value=inc_val,
                source=source,
            ))

    # Professional summary — fill missing language variants
    merged_ps = existing.professional_summary.model_copy(deep=True)
    if not merged_ps.de and incoming.professional_summary.de:
        merged_ps = merged_ps.model_copy(update={"de": incoming.professional_summary.de})
        all_added.append("professional_summary.de")
    if not merged_ps.en and incoming.professional_summary.en:
        merged_ps = merged_ps.model_copy(update={"en": incoming.professional_summary.en})
        all_added.append("professional_summary.en")

    # Publications — append non-duplicate (match on title)
    merged_pubs = list(existing.publications) + [
        p for p in incoming.publications
        if not any(ep.title.lower() == p.title.lower() for ep in existing.publications)
    ]

    # Volunteer activities — append non-duplicate (match on role + organization)
    merged_vol = list(existing.volunteer_activities) + [
        v for v in incoming.volunteer_activities
        if not any(
            ev.role.lower() == v.role.lower()
            and ev.organization.lower() == v.organization.lower()
            for ev in existing.volunteer_activities
        )
    ]

    # Projects — additive merge (US172 / ADR-013)
    merged_projects = _merge_projects(existing.projects, incoming.projects)

    merged_profile = existing.model_copy(
        deep=True,
        update={
            "personal_info": merged_pi,
            "professional_summary": merged_ps,
            "work_experience": merged_work,
            "education": merged_edu,
            "certifications": merged_certs,
            "skills": merged_skills,
            "languages": merged_langs,
            "publications": merged_pubs,
            "volunteer_activities": merged_vol,
            "projects": merged_projects,
        },
    )

    # Flagged conflicts are auto-decisions too — surface them as "we kept X, you may
    # want to verify" rows on the review surface (JF-M-3.2).
    for _c in all_conflicts:
        all_changes.append(FieldChange(
            section=_c.section, field=_c.field, action="updated",
            old_value=_c.existing_value, new_value=_c.incoming_value,
            rationale="Values differ between sources — flagged for your review.",
            rationale_key="conflict_flagged",
        ))

    return MergeResult(
        merged_profile=merged_profile,
        added=all_added,
        conflicts=all_conflicts,
        changes=all_changes,
        reconciliation=compute_merge_reconciliation(incoming, merged_profile),
    )
