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
Interview state machine — four nodes (ADR 004, Iteration 14).

Node flow:
    GapDetector → QuestionGenerator → [REST break] → ResponseParser → ProfileUpdater
                       ↑_______________________________________________|
                       (loop until all gaps addressed, done-signal, or hard ceiling)

MODE A (Targeted Gap-Fill):
    GapDetector consumes a GapAnalysis — returns C-first, then B gaps.
    QuestionGenerator produces gap-targeted questions.

MODE B (Guided Build):
    GapDetector produces a section build-plan from _VALID_SECTIONS weighted by JD relevance.
    QuestionGenerator produces section-building questions.

State is persisted as JSONB in interview_sessions.state between HTTP calls.
"""

import hashlib

from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.prompts.interview import (
    FOLLOW_UP_QUESTION_SYSTEM_PROMPT,
    GUIDED_QUESTION_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    RESPONSE_PARSER_SYSTEM_PROMPT,
    build_follow_up_question_prompt,
    build_guided_question_prompt,
    build_question_prompt,
    build_response_parser_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.schemas.session import ConflictSummary, InterviewState

# Sections included in a MODE B guided build, in default priority order.
# JD-relevance weighting is applied in gap_detector_mode_b() at session creation.
_MODE_B_CORE_SECTIONS = [
    "work_experience",
    "skills",
    "education",
    "personal_info",
    "languages",
    "certifications",
    "professional_summary",
]

# Sections added to MODE B only when the JD signals relevance
_MODE_B_EXTENDED_SECTIONS = ["publications", "volunteer_activities"]


# ---------------------------------------------------------------------------
# Node: GapDetector — MODE A
# ---------------------------------------------------------------------------


def gap_detector(
    gap_analysis: GapAnalysis,
) -> tuple[list[str], dict[str, str], dict]:
    """Return (ordered_cluster_ids, cluster_categories, clusters_by_id).

    Priority order:
      1. Category C clusters — highest value; interview must ask about these
      2. Category B clusters — confirm inferred experience
    Reads from gap_analysis.gap_clusters (set by cluster_gaps() in services/gap.py).

    Returns:
        cluster_ids: list[str] ordered C-first then B
        cluster_categories: dict mapping cluster_id to "B" or "C"
        clusters_by_id: dict mapping cluster_id to the full GapCluster dict
    """
    clusters: list[dict] = list(gap_analysis.gap_clusters or [])
    c_clusters = [c for c in clusters if c.get("category") == "C"]
    b_clusters = [c for c in clusters if c.get("category") == "B"]
    ordered = c_clusters + b_clusters

    cluster_ids = [c["id"] for c in ordered]
    categories = {c["id"]: c["category"] for c in ordered}
    by_id = {c["id"]: c for c in ordered}
    return cluster_ids, categories, by_id


# ---------------------------------------------------------------------------
# Node: GapDetector — MODE B
# ---------------------------------------------------------------------------


def gap_detector_mode_b(job_analysis: JobAnalysis) -> list[str]:
    """Return ordered section names for a MODE B guided build.

    Starts from _MODE_B_CORE_SECTIONS and promotes sections that are
    directly signalled by the JD (certifications, publications, languages).

    Returns:
        sections: ordered list of _VALID_SECTIONS keys to ask about
    """
    sections = list(_MODE_B_CORE_SECTIONS)

    jd_text = " ".join(
        (job_analysis.required_skills or [])
        + (job_analysis.nice_to_have_skills or [])
        + [job_analysis.role_title or ""]
    ).lower()

    # Promote certifications if JD mentions cert keywords
    cert_signals = {"certif", "zertif", "pmp", "aws certified", "azure certified", "cissp"}
    if any(sig in jd_text for sig in cert_signals) and "certifications" not in sections[:3]:
        sections.remove("certifications")
        sections.insert(2, "certifications")

    # Add extended sections if JD signals academic/research context
    research_signals = {"phd", "doktor", "publikation", "publication", "research", "forschung"}
    if any(sig in jd_text for sig in research_signals):
        sections.append("publications")

    # Add volunteer only for nonprofit/social-impact roles
    social_signals = {"ngo", "nonprofit", "gemeinnützig", "ehrenamt", "volunteer"}
    if any(sig in jd_text for sig in social_signals):
        sections.append("volunteer_activities")

    return sections


# ---------------------------------------------------------------------------
# Node: QuestionGenerator
# ---------------------------------------------------------------------------


async def question_generator_with_profile(
    state: InterviewState,
    profile: dict,
    provider: LLMProvider,
    gap_category: str | None = None,
    job_context: dict | None = None,
    follow_up_hint: str | None = None,
) -> dict:
    """Generate the next question based on mode and context.

    Returns:
        {"question": str, "choices": list[str] | None}

    MODE A: cluster-aware question with potential choices (uses aparse_json)
    MODE B: section-building question (uses acomplete, choices always None)
    Follow-up: lateral-probe question (uses acomplete, choices always None)
    """
    mode = state.get("mode", "targeted")

    if follow_up_hint:
        cluster_id = state["critical_gaps"][state["current_gap_index"]]
        clusters_by_id = state.get("gap_clusters_by_id") or {}
        cluster = clusters_by_id.get(cluster_id, {"label": cluster_id, "gaps": []})
        gap_label = cluster.get("label", cluster_id)
        text = await provider.acomplete(
            build_follow_up_question_prompt(
                gap_label,
                follow_up_hint,
                profile,
                state["messages"],
                gap_category=gap_category,
            ),
            system=FOLLOW_UP_QUESTION_SYSTEM_PROMPT,
            temperature=0.4,
            max_tokens=256,
        )
        return {"question": text.strip(), "choices": None}

    if mode == "guided":
        section = state["critical_gaps"][state["current_gap_index"]]
        text = await provider.acomplete(
            build_guided_question_prompt(
                section,
                job_context or {},
                state["messages"],
            ),
            system=GUIDED_QUESTION_SYSTEM_PROMPT,
            temperature=0.4,
            max_tokens=256,
        )
        return {"question": text.strip(), "choices": None}

    # MODE A: cluster-aware question with potential choices
    cluster_id = state["critical_gaps"][state["current_gap_index"]]
    clusters_by_id = state.get("gap_clusters_by_id") or {}
    cluster = clusters_by_id.get(
        cluster_id,
        {"id": cluster_id, "label": cluster_id, "gaps": [], "jd_skills": [], "jd_context": ""},
    )

    data: dict = await provider.aparse_json(
        build_question_prompt(cluster, profile, state["messages"], gap_category=gap_category),
        system=QUESTION_SYSTEM_PROMPT,
        temperature=0.4,
    )
    question = str(data.get("question", "")).strip()
    raw_choices = data.get("choices")
    choices = raw_choices if isinstance(raw_choices, list) and raw_choices else None
    return {"question": question, "choices": choices}


async def question_generator(
    state: InterviewState,
    provider: LLMProvider,
) -> dict:
    """Generate a targeted question for the current gap (no profile context).

    Kept for backwards compatibility — prefer question_generator_with_profile.
    Returns: {"question": str, "choices": None}
    """
    cluster_id = state["critical_gaps"][state["current_gap_index"]]
    clusters_by_id = state.get("gap_clusters_by_id") or {}
    cluster = clusters_by_id.get(
        cluster_id,
        {"id": cluster_id, "label": cluster_id, "gaps": [], "jd_skills": [], "jd_context": ""},
    )
    data: dict = await provider.aparse_json(
        build_question_prompt(cluster, {}, state["messages"]),
        system=QUESTION_SYSTEM_PROMPT,
        temperature=0.4,
    )
    return {"question": str(data.get("question", "")).strip(), "choices": None}


# ---------------------------------------------------------------------------
# Node: ResponseParser
# ---------------------------------------------------------------------------


async def response_parser(
    cluster_label: str,
    question: str,
    answer: str,
    provider: LLMProvider,
) -> dict:
    """Extract structured profile data from the user's free-text answer.

    Returns a dict with keys:
        skills_to_add, work_history_to_add, certifications_to_add,
        languages_to_add, education_to_add, gap_resolution, follow_up_hint,
        gap_addressed  (backward compat — derived from gap_resolution != "none")
    """
    data = await provider.aparse_json(
        build_response_parser_prompt(cluster_label, question, answer),
        system=RESPONSE_PARSER_SYSTEM_PROMPT,
        temperature=0.1,
    )
    gap_resolution = data.get("gap_resolution", "none")
    if gap_resolution not in ("full", "partial", "declined", "none"):
        gap_resolution = "none"
    return {
        "skills_to_add": data.get("skills_to_add", []),
        "work_history_to_add": data.get("work_history_to_add", []),
        "certifications_to_add": data.get("certifications_to_add", []),
        "languages_to_add": data.get("languages_to_add", []),
        "education_to_add": data.get("education_to_add", []),
        "gap_resolution": gap_resolution,
        "follow_up_hint": data.get("follow_up_hint") if isinstance(data.get("follow_up_hint"), str) else None,
        "gap_addressed": gap_resolution != "none",
    }


# ---------------------------------------------------------------------------
# Node: ProfileUpdater
# ---------------------------------------------------------------------------


def profile_updater(
    current_profile: dict, patch: dict
) -> tuple[dict, list[ConflictSummary]]:
    """Merge extracted data into the MasterProfile using intelligent merge rules.

    Returns (updated_profile, conflicts) — conflicts are surfaced when a
    work-experience entry for the same (company, role) carries contradicting dates.

    Rules (ADR 013):
    - skills: union — add new skills, never remove existing ones
    - work_experience: append entries whose (company, role) pair is not already present;
      date contradictions on matching entries are reported as ConflictSummary records
    - No field is ever overwritten if it already has a non-empty value
    """
    profile = dict(current_profile)
    conflicts: list[ConflictSummary] = []

    # --- Skills: union merge ---
    existing_skills = {_skill_name(s).lower() for s in profile.get("skills", [])}
    new_skills = [
        s for s in patch.get("skills_to_add", []) if _skill_name(s).lower() not in existing_skills
    ]
    if new_skills:
        profile["skills"] = list(profile.get("skills", [])) + new_skills

    # --- Work experience: append-only, detect date conflicts on matching entries ---
    existing_work = profile.get("work_experience", [])
    existing_by_key: dict[tuple[str, str], dict] = {
        (_norm(e.get("company")), _norm(e.get("role"))): e for e in existing_work
    }
    additions = []
    for entry in patch.get("work_history_to_add", []):
        key = (_norm(entry.get("company")), _norm(entry.get("role")))
        if not key[1]:
            continue
        if key in existing_by_key:
            existing_entry = existing_by_key[key]
            # Detect contradicting start_date
            old_start = existing_entry.get("start_date") or ""
            new_start = entry.get("start_date") or ""
            if old_start and new_start and (old_start + "-01")[:7] != (new_start + "-01")[:7]:
                field = f"{key[0]} / {key[1]} start_date"
                conflict_id = hashlib.md5(f"{field}:{old_start}".encode()).hexdigest()[:12]
                conflicts.append(
                    ConflictSummary(
                        conflict_id=conflict_id,
                        field=field,
                        old_value=old_start,
                        new_value=new_start,
                    )
                )
        else:
            additions.append(entry)
            existing_by_key[key] = entry

    if additions:
        profile["work_experience"] = list(existing_work) + additions

    # --- Certifications: append if name not already present (case-insensitive) ---
    existing_cert_names = {
        (c.get("name") or "").lower() for c in profile.get("certifications", [])
    }
    new_certs = [
        c for c in patch.get("certifications_to_add", [])
        if (c.get("name") or "").lower() not in existing_cert_names
    ]
    if new_certs:
        profile["certifications"] = list(profile.get("certifications", [])) + new_certs

    # --- Languages: append if language not present; keep existing level ---
    existing_lang_names = {
        (l.get("language") or "").lower() for l in profile.get("languages", [])
    }
    new_langs = [
        l for l in patch.get("languages_to_add", [])
        if (l.get("language") or "").lower() not in existing_lang_names
    ]
    if new_langs:
        profile["languages"] = list(profile.get("languages", [])) + new_langs

    # --- Education: append if (institution, degree) pair not present (case-insensitive) ---
    existing_edu_keys = {
        (_norm(e.get("institution")), _norm(e.get("degree")))
        for e in profile.get("education", [])
    }
    new_edu = [
        e for e in patch.get("education_to_add", [])
        if (_norm(e.get("institution")), _norm(e.get("degree"))) not in existing_edu_keys
    ]
    if new_edu:
        profile["education"] = list(profile.get("education", [])) + new_edu

    return profile, conflicts


def _skill_name(s: str | dict) -> str:
    if isinstance(s, dict):
        return s.get("name", "")
    return str(s)


# ---------------------------------------------------------------------------
# Node: GapDetector — MODE C (Profile Enrich)
# ---------------------------------------------------------------------------


def gap_detector_mode_c(
    profile: dict,
    scope: str | None = None,
) -> list[str]:
    """Return ordered completeness gaps for a MODE C profile-enrich session.

    Scans profile JSONB for missing fields. Pure Python — no LLM call.

    Priority order per work entry:
      1. achievements[] empty
      2. team_size is None
      3. budget_managed is None
      4. industry_context empty/None
    Then: professional_summary (only when scope is None and work_experience exists)

    Fields in profile['_meta']['na_fields'] are excluded.

    scope: "work_experience:<company>:<role>" limits scan to one entry.
           professional_summary is excluded when scope is set.
    """
    na_fields: set[str] = set(
        (profile.get("_meta") or {}).get("na_fields", [])
    )
    gaps: list[str] = []
    work_experience = profile.get("work_experience") or []

    for entry in work_experience:
        company = (entry.get("company") or "").strip()
        role = (entry.get("role") or entry.get("title") or "").strip()
        label = f"{role} @ {company}".strip(" @")

        if scope:
            parts = scope.split(":", 2)
            if len(parts) == 3:
                scope_company, scope_role = parts[1].strip(), parts[2].strip()
                if (
                    company.lower() != scope_company.lower()
                    or role.lower() != scope_role.lower()
                ):
                    continue

        for gap_str, is_gap in [
            (f"achievements: {label}", not entry.get("achievements")),
            (f"team_size: {label}", entry.get("team_size") is None),
            (f"budget_managed: {label}", entry.get("budget_managed") is None),
            (f"industry_context: {label}", not entry.get("industry_context")),
        ]:
            if is_gap and gap_str not in na_fields:
                gaps.append(gap_str)

    if scope is None and work_experience:
        summary_gap = "professional_summary"
        if not profile.get("professional_summary") and summary_gap not in na_fields:
            gaps.append(summary_gap)

    return gaps


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()
