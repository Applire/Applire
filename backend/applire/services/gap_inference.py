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

# Prompt version: N/A — pure Python, no LLM calls
# Used by: services/gap.py → two-pass gap analysis (rule-based pre-pass)
#
# Deterministic rules applied before the LLM classifier call:
#   1. Direct skill match          → Category A (matched)
#   2. Tenure ≥ 4 years in role    → Category B candidate
#   3. Seniority threshold met     → Category B candidate
#   4. DACH context signal         → Category B candidate (generic)
#
# Adjacent skill inference and employer-context inference are LLM-only (MVP).
# A static skill taxonomy is deferred to V2 (arc42 §11.2 tech debt).

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class InferredCandidate:
    """A JD requirement that rule-based logic suggests the candidate likely meets."""

    requirement: str
    reason: str  # human-readable, passed to LLM as context


@dataclass
class PreClassification:
    """Output of the rule-based pre-pass. Passed as structured context to the LLM."""

    matched: list[str] = field(default_factory=list)
    """Requirements directly found in the profile (definite Category A)."""

    inferred_b: list[InferredCandidate] = field(default_factory=list)
    """Rule-inferred Category B candidates — LLM confirms or rejects."""

    unresolved: list[str] = field(default_factory=list)
    """Requirements with no rule-based signal — LLM classifies as B or C."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def pre_classify(job_analysis: dict, profile: dict) -> PreClassification:
    """
    Rule-based pre-pass over the JD requirements and candidate profile.

    Returns a PreClassification that the LLM classifier uses as structured context,
    reducing hallucination and focusing the LLM on judgement calls only.
    """
    required: list[str] = job_analysis.get("required_skills", [])
    keywords: list[str] = job_analysis.get("keywords", [])
    seniority: str = (job_analysis.get("seniority_level") or "").lower()

    # Combine required skills and keywords; deduplicate while preserving order
    all_requirements: list[str] = _dedup(required + keywords)

    profile_skill_names = _extract_skill_names(profile)
    work_entries: list[dict] = profile.get("work_experience", [])
    project_entries: list[dict] = profile.get("projects", [])
    volunteer_entries: list[dict] = profile.get("volunteer_activities", [])
    education_entries: list[dict] = profile.get("education", [])
    languages: list[dict] = profile.get("languages", [])

    matched: list[str] = []
    inferred_b: list[InferredCandidate] = []
    unresolved: list[str] = []

    # --- Rule 1: Direct skill match (Category A) ---
    for req in all_requirements:
        if _skill_matches(req, profile_skill_names):
            matched.append(req)

    matched_set = {r.lower() for r in matched}
    unmatched = [r for r in all_requirements if r.lower() not in matched_set]

    # --- Rule 2: Tenure ≥ 4 years signals domain depth (Category B candidate) ---
    # Intentionally work-only: this feeds employment-seniority gap inference,
    # which is role/job-oriented (tenure at a company, not volunteer tenure).
    long_tenures = _roles_with_long_tenure(work_entries, min_years=4)

    # --- Rule 3: Seniority threshold (Category B candidate) ---
    # Cross-kind: projects and volunteering count toward total years (ADR-044 / US172).
    all_experience_entries = work_entries + project_entries + volunteer_entries
    total_experience_years = _total_experience_years(all_experience_entries)
    seniority_met = _seniority_threshold_met(seniority, total_experience_years)

    # --- Rule 4: DACH context signal (Category B candidate for cultural/market reqs) ---
    dach_signal = _has_dach_context(education_entries, languages)

    # Build inferred_b candidates from unmatched requirements
    for req in unmatched:
        reasons: list[str] = []

        req_lower = req.lower()
        req_tokens = [w for w in req_lower.split() if len(w) > 2]

        # Tenure signal: if any long-tenure role's industry/tech/responsibilities overlaps with req
        for tenure_role in long_tenures:
            industry = (tenure_role.get("industry_context") or "").lower()
            technologies = [t.lower() for t in (tenure_role.get("technologies") or [])]
            company = (tenure_role.get("company") or "").lower()
            responsibilities_text = " ".join(
                (r or "").lower() for r in (tenure_role.get("responsibilities") or [])
            )
            achievements_text = " ".join(
                (a or "").lower() for a in (tenure_role.get("achievements") or [])
            )
            if (
                req_lower in industry
                or any(token in industry for token in req_tokens)
                or any(req_lower in t for t in technologies)
                or req_lower in company
                or req_lower in responsibilities_text
                or any(token in responsibilities_text for token in req_tokens)
                or req_lower in achievements_text
                or any(token in achievements_text for token in req_tokens)
            ):
                years = tenure_role["_tenure_years"]
                role_name = tenure_role.get("role") or "role"
                company_name = tenure_role.get("company") or "employer"
                reasons.append(
                    f"Tenure {years:.0f}y as {role_name} at {company_name} suggests domain familiarity"
                )

        # Seniority signal: senior-level requirements may be met by years of experience
        if seniority_met and any(
            kw in req_lower
            for kw in ("senior", "lead", "principal", "architect", "head of", "manager")
        ):
            reasons.append(
                f"{total_experience_years:.0f} years total experience meets seniority bar"
            )

        # DACH signal: market/culture/language requirements
        if dach_signal and any(
            kw in req_lower
            for kw in (
                "german",
                "deutsch",
                "dach",
                "austrian",
                "swiss",
                "business culture",
                "stakeholder",
            )
        ):
            reasons.append("DACH education or native German speaker detected in profile")

        if reasons:
            inferred_b.append(
                InferredCandidate(
                    requirement=req,
                    reason="; ".join(reasons),
                )
            )
        else:
            unresolved.append(req)

    return PreClassification(
        matched=matched,
        inferred_b=inferred_b,
        unresolved=unresolved,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_skill_names(profile: dict) -> set[str]:
    """Collect normalised skill names from profile.skills (str or dict)."""
    names: set[str] = set()
    for skill in profile.get("skills", []):
        if isinstance(skill, dict):
            name = skill.get("name") or skill.get("category") or ""
        else:
            name = str(skill)
        if name:
            names.add(name.lower())
    return names


def _skill_matches(requirement: str, profile_skills: set[str]) -> bool:
    """True if the requirement string has a direct case-insensitive match in profile skills."""
    req_lower = requirement.lower()
    if req_lower in profile_skills:
        return True
    # Check if any profile skill contains the requirement as a substring (e.g. "Python" in "Python 3.11")
    return any(req_lower in s or s in req_lower for s in profile_skills)


def _parse_date(value: str | None) -> date | None:
    """Parse YYYY-MM, YYYY, or ISO date strings into a date object."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _tenure_years(entry: dict) -> float:
    """Return the duration of a work entry in years. Treats None end_date as today."""
    start = _parse_date(entry.get("start_date"))
    if start is None:
        return 0.0
    end_raw = entry.get("end_date")
    end = _parse_date(end_raw) if end_raw else date.today()
    delta_days = (end - start).days
    return max(0.0, delta_days / 365.25)


def _roles_with_long_tenure(work_entries: list[dict], min_years: float) -> list[dict]:
    """Return work entries enriched with _tenure_years where tenure >= min_years."""
    result = []
    for entry in work_entries:
        years = _tenure_years(entry)
        if years >= min_years:
            enriched = dict(entry)
            enriched["_tenure_years"] = years
            result.append(enriched)
    return result


def _total_experience_years(entries: list[dict]) -> float:
    """Total experience as the de-overlapped union of all entry date-spans
    (jobs, projects, volunteering). Concurrent spans are NOT double-counted."""
    spans = []
    for e in entries:
        start = _parse_date(e.get("start_date"))
        if start is None:
            continue
        end_raw = e.get("end_date")
        end = _parse_date(end_raw) if end_raw else date.today()
        if end is None or end < start:
            continue
        spans.append((start, end))
    if not spans:
        return 0.0
    spans.sort()
    merged_days = 0
    cur_start, cur_end = spans[0]
    for s, e in spans[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            merged_days += (cur_end - cur_start).days
            cur_start, cur_end = s, e
    merged_days += (cur_end - cur_start).days
    return max(0.0, merged_days / 365.25)


_SENIORITY_THRESHOLDS: dict[str, float] = {
    "junior": 0.0,
    "entry": 0.0,
    "mid": 3.0,
    "intermediate": 3.0,
    "senior": 5.0,
    "lead": 8.0,
    "principal": 8.0,
    "staff": 8.0,
    "director": 10.0,
    "vp": 10.0,
    "head": 10.0,
}


def _seniority_threshold_met(seniority_level: str, total_years: float) -> bool:
    """True if candidate's total experience meets the JD's seniority level threshold."""
    for key, threshold in _SENIORITY_THRESHOLDS.items():
        if key in seniority_level:
            return total_years >= threshold
    return False


_DACH_COUNTRIES = {"germany", "austria", "switzerland", "deutschland", "österreich", "schweiz"}
_DACH_NATIVE_LANGUAGES = {"german", "deutsch", "french", "français", "italian", "italiano"}
_NATIVE_PROFICIENCIES = {
    "native_or_bilingual",
    "native",
    "bilingual",
    "muttersprachler",
    "c2",
}


def _has_dach_context(education_entries: list[dict], languages: list[dict]) -> bool:
    """
    True if the profile contains a DACH context signal:
    - Education at an institution in a DACH country, OR
    - Native/bilingual proficiency in German, French, or Italian
    """
    for edu in education_entries:
        institution = (edu.get("institution") or edu.get("school_name") or "").lower()
        country = (edu.get("country") or "").lower()
        if country in _DACH_COUNTRIES:
            return True
        # Heuristic: well-known DACH university keywords
        if any(kw in institution for kw in ("universität", "hochschule", "tu münchen", "eth", "epfl", "wu wien")):
            return True

    for lang in languages:
        lang_name = (lang.get("language") or lang.get("name") or "").lower()
        proficiency = (lang.get("proficiency") or lang.get("level") or "").lower().replace(" ", "_")
        if lang_name in _DACH_NATIVE_LANGUAGES and proficiency in _NATIVE_PROFICIENCIES:
            return True

    return False


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
