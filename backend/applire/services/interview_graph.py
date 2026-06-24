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
from applire.constants import INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES
from applire.prompts.interview import (
    FOLLOW_UP_QUESTION_SYSTEM_PROMPT,
    GUIDED_QUESTION_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    RESPONSE_PARSER_SYSTEM_PROMPT,
    build_field_gap_question_prompt,
    build_follow_up_question_prompt,
    build_guided_question_prompt,
    build_question_prompt,
    build_response_parser_prompt,
    language_name,
    with_language,
)
from applire.prompts.review_question_language import (
    QUESTION_LANGUAGE_REFINEMENT_PROMPT,
    QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT,
    build_question_language_refinement_prompt,
    build_question_language_review_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import FieldChange
from applire.schemas.session import ConflictSummary, InterviewState
from applire.services.profile.merge import (
    company_names_match,
    dates_overlap,
    roles_are_same,
)
from applire.services.reviewer import review_and_refine

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


def _find_work_entry(profile: dict, label: str) -> dict | None:
    """Find the work entry whose '<role> @ <company>' label matches (case-insensitive)."""
    for entry in profile.get("work_experience") or []:
        role = (entry.get("role") or entry.get("title") or "").strip()
        company = (entry.get("company") or "").strip()
        if f"{role} @ {company}".strip(" @").lower() == label.lower():
            return entry
    return None


async def _review_question_language(
    draft: dict, lang: str, provider: LLMProvider
) -> dict:
    """Verify the drafted question/choices are in `lang`; regenerate on mismatch.

    Reuses the ADR-021 review_and_refine loop. Never raises; returns the last
    draft on retry exhaustion. No-op when the retry budget is 0.
    """
    if INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES <= 0:
        return draft
    lang_name = language_name(lang)
    return await review_and_refine(
        source=lang_name,
        draft=draft,
        generator_prompt_fn=build_question_language_refinement_prompt,
        generator_system=QUESTION_LANGUAGE_REFINEMENT_PROMPT,
        reviewer_prompt_fn=build_question_language_review_prompt,
        reviewer_system=QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT,
        provider=provider,
        max_retries=INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES,
        generator_max_tokens=256,
        chain_id="interview_question",
    )


async def question_generator_with_profile(
    state: InterviewState,
    profile: dict,
    provider: LLMProvider,
    gap_category: str | None = None,
    job_context: dict | None = None,
    follow_up_hint: str | None = None,
    lang: str = "en",
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
            system=with_language(FOLLOW_UP_QUESTION_SYSTEM_PROMPT, lang),
            temperature=0.4,
            max_tokens=256,
        )
        draft = {"question": text.strip(), "choices": None}
        return await _review_question_language(draft, lang, provider)

    if state.get("mode") == "profile_enrich" and not follow_up_hint:
        gap = state["critical_gaps"][state["current_gap_index"]]
        field, _, label = gap.partition(":")
        field, label = field.strip(), label.strip()
        entry = _find_work_entry(profile, label)
        if field != "professional_summary" and entry is not None:
            text = await provider.acomplete(
                build_field_gap_question_prompt(field, entry, state["messages"]),
                system=with_language(GUIDED_QUESTION_SYSTEM_PROMPT, lang),
                temperature=0.4,
                max_tokens=256,
            )
            draft = {"question": text.strip(), "choices": None}
            return await _review_question_language(draft, lang, provider)
        # professional_summary or unmatched label → fall through to existing path

    if mode == "guided":
        section = state["critical_gaps"][state["current_gap_index"]]
        text = await provider.acomplete(
            build_guided_question_prompt(
                section,
                job_context or {},
                state["messages"],
            ),
            system=with_language(GUIDED_QUESTION_SYSTEM_PROMPT, lang),
            temperature=0.4,
            max_tokens=256,
        )
        draft = {"question": text.strip(), "choices": None}
        return await _review_question_language(draft, lang, provider)

    # MODE A: cluster-aware question with potential choices
    cluster_id = state["critical_gaps"][state["current_gap_index"]]
    clusters_by_id = state.get("gap_clusters_by_id") or {}
    cluster = clusters_by_id.get(
        cluster_id,
        {"id": cluster_id, "label": cluster_id, "gaps": [], "jd_skills": [], "jd_context": ""},
    )

    data: dict = await provider.aparse_json(
        build_question_prompt(cluster, profile, state["messages"], gap_category=gap_category),
        system=with_language(QUESTION_SYSTEM_PROMPT, lang),
        temperature=0.4,
    )
    question = str(data.get("question", "")).strip()
    raw_choices = data.get("choices")
    draft = {"question": question, "choices": raw_choices if isinstance(raw_choices, list) and raw_choices else None}
    reviewed = await _review_question_language(draft, lang, provider)
    rc = reviewed.get("choices")
    reviewed["choices"] = rc if isinstance(rc, list) and rc else None
    reviewed["question"] = str(reviewed.get("question", "")).strip()
    return reviewed


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
    - work_experience: an entry naming a known employer (fuzzy company match,
      shared with the CV-upload merge) enriches the existing entry — bullets
      accumulate into achievements, a differing title becomes a role_alias.
      Only a dated entry that does not overlap any stint at that employer
      (or a new employer entirely) is appended as a new position. Date
      contradictions on matching entries are reported as ConflictSummary records.
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

    # --- Work experience: enrich matching employers, append only genuine new positions ---
    existing_work = [dict(e) for e in profile.get("work_experience", [])]
    for entry in patch.get("work_history_to_add", []):
        if not _norm(entry.get("role")):
            continue
        bullets = [
            b.strip() for b in (entry.get("bullets") or [])
            if isinstance(b, str) and b.strip()
        ]

        match = _find_matching_work_entry(existing_work, entry)
        if match is None:
            # New employer (or a dated, non-overlapping stint at a known one)
            addition = {k: v for k, v in entry.items() if k != "bullets"}
            if bullets:
                addition["achievements"] = bullets
            existing_work.append(addition)
            continue

        # Known employer → enrich the existing entry, never create a duplicate
        existing_achievements = list(match.get("achievements") or [])
        seen = {a.strip().lower() for a in existing_achievements}
        for b in bullets:
            if b.lower() not in seen:
                existing_achievements.append(b)
                seen.add(b.lower())
        match["achievements"] = existing_achievements

        new_role = (entry.get("role") or "").strip()
        known_titles = {
            t.strip().lower()
            for t in [match.get("role") or ""] + list(match.get("role_aliases") or [])
        }
        if new_role and new_role.lower() not in known_titles:
            match["role_aliases"] = list(match.get("role_aliases") or []) + [new_role]

        # Detect contradicting start_date on same-role entries only — a loose
        # role paraphrase from an answer is not evidence about dates.
        if _norm(entry.get("role")) == _norm(match.get("role")):
            old_start = match.get("start_date") or ""
            new_start = entry.get("start_date") or ""
            if old_start and new_start and (old_start + "-01")[:7] != (new_start + "-01")[:7]:
                field = f"{_norm(match.get('company'))} / {_norm(match.get('role'))} start_date"
                conflict_id = hashlib.md5(f"{field}:{old_start}".encode()).hexdigest()[:12]
                conflicts.append(
                    ConflictSummary(
                        conflict_id=conflict_id,
                        field=field,
                        old_value=old_start,
                        new_value=new_start,
                    )
                )

    profile["work_experience"] = existing_work

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


def interview_field_changes(before: dict, after: dict) -> list[FieldChange]:
    """US148/ADR-040 (JF-M-5.2) — structured record of what an interview answer added
    to the profile, for the "what we added from your answers" surface and the durable
    decision trail. Diffs the merged profile against the pre-merge one so only genuine
    additions are recorded (a paraphrase already in the profile is not).
    """
    changes: list[FieldChange] = []
    _added = "Added from your interview answer."

    # Skills (string or {name}) added
    before_skills = {_skill_name(s).strip().lower() for s in (before.get("skills") or [])}
    for s in after.get("skills") or []:
        name = _skill_name(s).strip()
        if name and name.lower() not in before_skills:
            changes.append(FieldChange(
                section="skills", field="skills", action="added",
                new_value=name, rationale=_added,
                rationale_key="interview_added",
            ))

    # Certifications added
    before_certs = {(c.get("name") or "").strip().lower() for c in (before.get("certifications") or [])}
    for c in after.get("certifications") or []:
        name = (c.get("name") or "").strip()
        if name and name.lower() not in before_certs:
            changes.append(FieldChange(
                section="certifications", field="certifications", action="added",
                new_value=name, rationale=_added,
                rationale_key="interview_added",
            ))

    # Work experience — new positions (added) and enriched existing ones (merged)
    before_by_company: dict[str, list[dict]] = {}
    for e in before.get("work_experience") or []:
        before_by_company.setdefault((e.get("company") or "").strip().lower(), []).append(e)
    for e in after.get("work_experience") or []:
        company = (e.get("company") or "").strip()
        ckey = company.lower()
        role = (e.get("role") or "").strip()
        if ckey not in before_by_company:
            changes.append(FieldChange(
                section="work_experience", field="work_experience", action="added",
                new_value=f"{role} @ {company}".strip(" @"),
                rationale="New position from your interview answer.",
                rationale_key="interview_new_position",
            ))
            continue
        # Known employer — record if achievements grew (details added from the answer)
        before_ach = max((len(x.get("achievements") or []) for x in before_by_company[ckey]), default=0)
        if len(e.get("achievements") or []) > before_ach:
            changes.append(FieldChange(
                section="work_experience", field="achievements", action="merged",
                new_value=f"{role} @ {company}".strip(" @"),
                rationale="Details added to this position from your interview answer.",
                rationale_key="interview_details_added",
            ))

    return changes


def _find_matching_work_entry(existing: list[dict], entry: dict) -> dict | None:
    """Return the existing work entry an answer-extracted entry refers to.

    Same employer (fuzzy, shared with the CV-upload merge) + same role/alias
    wins; otherwise same employer with overlapping or unknown dates — an
    undated answer mentioning a known employer describes an existing stint,
    not a new position. Returns None for a genuinely new position.
    """
    candidates = [
        ex for ex in existing
        if company_names_match(ex.get("company") or "", entry.get("company") or "")
    ]
    if not candidates:
        return None

    role = _norm(entry.get("role"))
    for ex in candidates:
        known_titles = {_norm(ex.get("role"))} | {
            _norm(a) for a in (ex.get("role_aliases") or [])
        }
        if role in known_titles:
            return ex

    # Same employer + overlapping dates is NOT enough on its own: a substantively
    # different title at the same employer is a separate role (a promotion), not
    # the existing stint — collapsing them is the #71 / F2 bug. Require the title
    # to denote the same role (identical or a seniority refinement) before fusing.
    overlapping = [
        ex for ex in candidates
        if roles_are_same(ex.get("role") or "", entry.get("role") or "")
        and dates_overlap(
            ex.get("start_date"), ex.get("end_date"),
            entry.get("start_date"), entry.get("end_date"),
        )
    ]
    if not overlapping:
        return None
    # Prefer the current (open-ended) or most recent stint
    overlapping.sort(
        key=lambda ex: ((ex.get("end_date") or "9999-12") + "-12")[:7], reverse=True
    )
    return overlapping[0]


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
    """Ordered MODE C completeness gaps. Delegates to the unified completeness
    model (US179) so the score and this list derive from one source (ADR-041)."""
    from applire.services.profile.completeness import field_gaps
    return field_gaps(profile, scope=scope)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


# ---------------------------------------------------------------------------
# US163 (E033 / ADR-041 amended) — deferred integrity gate as a blocking question
# ---------------------------------------------------------------------------
#
# A parked US167 gate (not-a-CV / name divergence) is the ONLY non-job-relevant
# forcing left in the interview. It is injected ahead of every JD gap as a
# mandatory pseudo-gap so the user can never tailor a CV from a profile whose
# origin they never confirmed. The prompt is deterministic (no LLM) — the system
# only asks the user to decide; it never decides identity itself.

import re

GATE_CATEGORY = "GATE"
_GATE_PREFIX = "gate:"

# Deterministic confirm copy, bilingual (ADR-038). Mirrors the US167 dialog text.
_GATE_COPY = {
    "en": {
        "name_divergence": (
            "Before we tailor your CV: a parked upload is for '{cv}', but your "
            "profile is '{account}'. Is that CV yours and should we add it?"
        ),
        "not_a_cv": (
            "Before we tailor your CV: a parked upload doesn't look like a CV. "
            "Add it to your profile anyway?"
        ),
        "merge": "Yes — it's mine, add it",
        "discard": "No — discard it",
    },
    "de": {
        "name_divergence": (
            "Bevor wir deinen Lebenslauf anpassen: Ein zurückgestellter Upload "
            "lautet auf '{cv}', dein Profil aber auf '{account}'. Gehört dieser "
            "Lebenslauf dir und sollen wir ihn übernehmen?"
        ),
        "not_a_cv": (
            "Bevor wir deinen Lebenslauf anpassen: Ein zurückgestellter Upload "
            "sieht nicht wie ein Lebenslauf aus. Trotzdem zum Profil hinzufügen?"
        ),
        "merge": "Ja — er ist meiner, übernehmen",
        "discard": "Nein — verwerfen",
    },
}

# Answer interpretation. Possessive words are deliberately excluded so that a
# negation like "nein, der ist nicht meiner" resolves to discard, not ambiguity.
_MERGE_WORDS = frozenset({
    "yes", "yep", "yeah", "sure", "merge", "keep", "add", "anyway", "correct",
    "ja", "jap", "übernehmen", "uebernehmen", "behalten", "hinzufügen",
    "hinzufuegen", "richtig", "stimmt", "trotzdem",
})
_DISCARD_WORDS = frozenset({
    "no", "nope", "discard", "drop", "delete", "remove", "wrong",
    "nein", "verwerfen", "löschen", "loeschen", "entfernen", "falsch",
})


def is_gate_cluster(cluster_id: str) -> bool:
    """True if a critical-gaps entry is a deferred-gate pseudo-gap (US163)."""
    return isinstance(cluster_id, str) and cluster_id.startswith(_GATE_PREFIX)


def gate_question(
    gate: str,
    account_name: str | None,
    cv_name: str | None,
    lang: str = "en",
) -> dict:
    """Deterministic (no-LLM) confirm prompt + two safe choices for a held gate."""
    copy = _GATE_COPY.get(lang, _GATE_COPY["en"])
    template = copy.get(gate, copy["name_divergence"])
    question = template.format(cv=cv_name or "—", account=account_name or "—")
    return {"question": question, "choices": [copy["merge"], copy["discard"]]}


def build_gate_clusters(
    gates: list[dict],
    lang: str = "en",
) -> tuple[list[str], dict, dict]:
    """Turn open parked gates into gate-first pseudo-clusters.

    ``gates`` items: ``{upload_id, gate, account_name, cv_name}``. Returns the
    same ``(cluster_ids, categories, clusters_by_id)`` shape the GapDetector
    yields, so the gate ids can be prepended to ``critical_gaps`` directly.
    """
    cluster_ids: list[str] = []
    categories: dict[str, str] = {}
    by_id: dict[str, dict] = {}
    for g in gates:
        cid = f"{_GATE_PREFIX}{g['upload_id']}"
        q = gate_question(g["gate"], g.get("account_name"), g.get("cv_name"), lang)
        cluster_ids.append(cid)
        categories[cid] = GATE_CATEGORY
        by_id[cid] = {
            "id": cid,
            "kind": "gate",
            "gate": g["gate"],
            "upload_id": str(g["upload_id"]),
            "account_name": g.get("account_name"),
            "cv_name": g.get("cv_name"),
            "label": q["question"],
            "question": q["question"],
            "choices": q["choices"],
        }
    return cluster_ids, categories, by_id


def interpret_gate_answer(answer: str) -> str:
    """Map a free-text / choice answer to ``"merge" | "discard" | "unclear"``.

    Conservative: a mixed or empty answer is ``"unclear"`` so the caller re-asks
    rather than guessing — the safe default is never to merge unconfirmed data.
    """
    tokens = set(re.findall(r"\w+", (answer or "").casefold()))
    merge = bool(tokens & _MERGE_WORDS)
    discard = bool(tokens & _DISCARD_WORDS)
    if merge and not discard:
        return "merge"
    if discard and not merge:
        return "discard"
    return "unclear"


# ---------------------------------------------------------------------------
# US165 (E033 / ADR-041) — a pending Tier-2 conflict as a profile-review question
# ---------------------------------------------------------------------------
#
# In the standalone profile-review interview (no JD), each unresolved ADR-013
# ``Conflict`` becomes a deterministic two-choice question: keep the value the
# profile already holds, or adopt the value an import proposed. The prompt is
# generated without the LLM — the system surfaces both values and asks the user
# to choose; it never decides a factual correction itself. The chosen side is
# applied through ``resolve_conflict`` (manual_edit EnrichmentRecord).

CONFLICT_CATEGORY = "CONFLICT"
_CONFLICT_PREFIX = "conflict:"

_CONFLICT_COPY = {
    "en": {
        "question": (
            "Your profile has two values for {section}.{field}: currently "
            "'{existing}', but an import suggested '{incoming}'. Which is correct?"
        ),
        "keep": "Keep current: {existing}",
        "use": "Use imported: {incoming}",
    },
    "de": {
        "question": (
            "Dein Profil hat zwei Werte für {section}.{field}: aktuell "
            "'{existing}', ein Import schlug aber '{incoming}' vor. Welcher stimmt?"
        ),
        "keep": "Aktuellen behalten: {existing}",
        "use": "Importierten übernehmen: {incoming}",
    },
}

# Answer interpretation. "keep" words map to the existing value, "use" words to
# the incoming one. Symmetric and conservative: a mixed or empty answer that also
# matches neither value verbatim is "unclear" so the caller re-asks.
_KEEP_WORDS = frozenset({
    "keep", "current", "currently", "existing", "mine", "original", "old", "leave",
    "behalten", "aktuell", "aktuellen", "beibehalten", "bisherig", "bisherigen",
    "alt", "alten", "lassen",
})
_USE_WORDS = frozenset({
    "use", "new", "imported", "import", "update", "change", "replace", "adopt",
    "übernehmen", "uebernehmen", "neu", "neuen", "importiert", "importierten",
    "aktualisieren", "ersetzen", "ändern", "aendern",
})


def is_conflict_cluster(cluster_id: str) -> bool:
    """True if a critical-gaps entry is a pending-conflict pseudo-gap (US165)."""
    return isinstance(cluster_id, str) and cluster_id.startswith(_CONFLICT_PREFIX)


def conflict_question(
    section: str,
    field: str,
    existing_value,
    incoming_value,
    lang: str = "en",
) -> dict:
    """Deterministic (no-LLM) correction prompt + the two value choices."""
    copy = _CONFLICT_COPY.get(lang, _CONFLICT_COPY["en"])
    fmt = dict(
        section=section, field=field,
        existing=existing_value, incoming=incoming_value,
    )
    return {
        "question": copy["question"].format(**fmt),
        "choices": [copy["keep"].format(**fmt), copy["use"].format(**fmt)],
    }


def build_conflict_clusters(
    conflicts: list[dict],
    lang: str = "en",
) -> tuple[list[str], dict, dict]:
    """Turn unresolved conflicts into profile-review pseudo-clusters.

    ``conflicts`` items: ``{conflict_id, section, field, existing_value,
    incoming_value}``. Returns the GapDetector-shaped ``(ids, categories,
    clusters_by_id)`` so the ids can populate ``critical_gaps`` directly.
    """
    cluster_ids: list[str] = []
    categories: dict[str, str] = {}
    by_id: dict[str, dict] = {}
    for c in conflicts:
        cid = f"{_CONFLICT_PREFIX}{c['conflict_id']}"
        q = conflict_question(
            c["section"], c["field"],
            c["existing_value"], c["incoming_value"], lang,
        )
        cluster_ids.append(cid)
        categories[cid] = CONFLICT_CATEGORY
        by_id[cid] = {
            "id": cid,
            "kind": "conflict",
            "conflict_id": c["conflict_id"],
            "section": c["section"],
            "field": c["field"],
            "existing_value": c["existing_value"],
            "incoming_value": c["incoming_value"],
            "label": q["question"],
            "question": q["question"],
            "choices": q["choices"],
        }
    return cluster_ids, categories, by_id


def interpret_conflict_answer(answer: str, existing_value, incoming_value) -> str:
    """Map an answer to ``"existing" | "incoming" | "unclear"``.

    First tries keep/use intent words; falls back to a verbatim match of the
    answer against one of the two values. Ambiguous → ``"unclear"`` (re-ask).
    """
    text = (answer or "").strip()
    tokens = set(re.findall(r"\w+", text.casefold()))
    keep = bool(tokens & _KEEP_WORDS)
    use = bool(tokens & _USE_WORDS)
    if keep and not use:
        return "existing"
    if use and not keep:
        return "incoming"

    # Verbatim value match (the user just typed the correct value).
    norm = text.casefold()
    matches_existing = bool(norm) and norm == str(existing_value or "").strip().casefold()
    matches_incoming = bool(norm) and norm == str(incoming_value or "").strip().casefold()
    if matches_existing and not matches_incoming:
        return "existing"
    if matches_incoming and not matches_existing:
        return "incoming"
    return "unclear"
