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

"""CV service — Iteration 17 (async generation)

generate_cv (17.12 / arc42 §5.3.4):
    Create GeneratedCV record with status='pending'.
    Enqueue _render_cv_background via FastAPI BackgroundTasks.
    Return immediately — caller polls GET /api/cv/{cv_id}/status.

get_cv_status:
    Return CVStatusResponse with current status + urls if ready.

get_cv_html / get_cv_pdf:
    Load GeneratedCV (must be 'ready'), render template / PDF.
    Both raise LookupError if status != 'ready' to prevent serving stale content.

_render_cv_background:
    Heavy LLM + Playwright work — runs outside the request lifecycle.
    Updates status: pending → generating → ready | failed.
    Creates its own DB session (original request session is closed).
"""

import base64 as _base64
import json as _json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from applire.services.cv_budget import BudgetResult
    from applire.storage.base import StorageProvider

from fastapi import BackgroundTasks
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.db.session import AsyncSessionLocal
from applire.models.cv import CVGenerationStatus, GeneratedCV
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.norms import DEFAULT_REGION, resolve_target_pages
from applire.prompts.cv_tailoring import (
    CV_TAILORING_REFINEMENT_PROMPT,
    SYSTEM_PROMPT,
    build_retry_prompt as _build_cv_retry_prompt,
    build_user_prompt,
)
from applire.prompts.review_cv_tailoring import (
    REVIEW_SYSTEM_PROMPT as _CV_REVIEW_SYSTEM_PROMPT,
    build_review_prompt as _build_cv_review_prompt,
)
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.capabilities import resolve_effective_output_cap
from applire.schemas.cv import (
    CVGenerateResponse,
    CVStatusResponse,
    CVTemplate,
    TailoredCertification,
    TailoredCVData,
    TailoredProjectEntry,
    TailoredWorkEntry,
)
from applire.prompts.review_cv_language import (
    CV_LANGUAGE_REFINEMENT_PROMPT,
    CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
    build_cv_language_refinement_prompt,
    build_cv_language_review_prompt,
)
from applire.prompts.interview import language_name
from applire.templates.labels import cv_labels
from applire.services.reviewer import review_and_refine
from applire.utils.language_detection import resolve_jd_language
from applire.services.profile.merge import _sort_work_by_date
from applire.constants import (
    CV_GENERATION_MAX_TOKENS,
    CV_LANGUAGE_REVIEW_MAX_RETRIES,
    CV_MAX_SKILLS,
    LLM_REVIEW_MAX_RETRIES,
    SEGMENT_MAX_TOKENS,
)
from applire.exceptions import LLMTimeoutError, LLMTruncatedError


def classify_generation_error(exc: BaseException) -> str:
    """Map an internal CV-generation failure to a STABLE machine code (ADR-047 §4).

    Honest-failure UX (PQ F6): the raw exception text — e.g. "Raise max_tokens or
    reduce reasoning" — must never reach the user. The catch site persists the raw
    message internally for ops, but the API surfaces only this code, which the frontend
    maps to a localized human message + retry affordance (error UI is "chrome", so
    localization lives on the frontend with the user's locale, not here).
    """
    if isinstance(exc, LLMTruncatedError):
        return "llm_truncated"
    if isinstance(exc, LLMTimeoutError):
        return "llm_timeout"
    return "generation_failed"


def _record_generation_failure(record, exc: BaseException) -> None:
    """Mark a generation record failed, keeping the raw exception text internal and
    setting a classified, user-safe error_code (ADR-047 §4 / PQ F6).

    error_message holds the raw text for ops/logs; error_code is what the API surfaces.
    Kept as a seam so the classification + split is unit-testable without a DB session.
    """
    record.status = CVGenerationStatus.failed.value
    record.error_message = str(exc)[:1000]
    record.error_code = classify_generation_error(exc)


def assemble_segmented_cv(outline: dict, sections: dict) -> dict:
    """Deterministically assemble outline-then-expand section pieces into a TailoredCVData
    dict (ADR-047 §1 / US189).

    Work history is ordered by ``outline['role_order']``; an entry the outline forgot is
    appended in input order (no silent data loss — ADR-040), and a stale id with no
    matching entry is skipped (nothing fabricated). Pure: no LLM, no I/O. The result is
    handed to the same downstream as the single-call path (``_nest_projects``, photo
    injection, the coherence + language review).
    """
    work_entries: list[dict] = list(sections.get("work_entries") or [])
    first_index_by_id: dict = {}
    for i, w in enumerate(work_entries):
        first_index_by_id.setdefault(w.get("id"), i)

    ordered: list[dict] = []
    placed: set[int] = set()
    for rid in outline.get("role_order") or []:
        i = first_index_by_id.get(rid)
        if i is not None and i not in placed:
            ordered.append(work_entries[i])
            placed.add(i)
    for i, w in enumerate(work_entries):  # entries the outline didn't order
        if i not in placed:
            ordered.append(w)
            placed.add(i)

    return {
        # contact is factual identity data sourced deterministically from the profile,
        # never LLM-generated per segment (ADR-040). Photo is injected downstream as today.
        "contact": sections.get("contact") or {},
        "summary": sections.get("summary") or "",
        "work_history": ordered,
        "skills": list(sections.get("skills") or []),
        "education": list(sections.get("education") or []),
        "languages": list(sections.get("languages") or []),
        "projects": list(sections.get("projects") or []),
    }


def _contact_from_profile(profile: dict) -> dict:
    """Source CV contact deterministically from the profile (ADR-040) — identity data is
    never LLM-generated per segment. Reads personal_info (or a flat contact block)."""
    pi = profile.get("personal_info") or profile.get("contact") or {}
    return {
        k: pi.get(k)
        for k in ("name", "email", "phone", "location", "linkedin")
        if pi.get(k) is not None
    }


async def generate_cv_segmented(
    job_analysis: dict,
    profile: dict,
    keyword_gaps: list[str],
    critical_gaps: list[str],
    *,
    output_language: str,
    provider: "LLMProvider",
    keyword_ledger: list[dict] | None = None,
    budget: "BudgetResult | None" = None,
) -> dict:
    """Outline-then-expand CV tailoring (ADR-047 §1 / US189) — the segmented path.

    One small outline call produces a shared tailoring directive; then one call per
    work-experience entry plus one each for summary / skills / education / projects, every
    call capped at ``SEGMENT_MAX_TOKENS`` so no single output is large. Factual fields
    (company, role, dates, contact) are carried deterministically from the profile (ADR-040)
    — section writers only produce tailored prose. Work order stays reverse-chronological
    (single-call rule-2 parity); the outline's role_order is advisory. The assembled dict is
    handed to the same coherence + language review as the single-call path by the caller.

    ``budget`` (E042/US237, ADR-051 §3) — the deterministic per-role bullet-count ceiling
    table, threaded into the outline call and each per-role work-section call so the model
    aims at the target page count directly. Not to be confused with the per-call TOKEN
    budget (``SEGMENT_MAX_TOKENS``) below — deliberately named ``token_budget`` to avoid
    the collision.
    """
    from applire.prompts.cv_segmented import (
        EDUCATION_SECTION_SYSTEM_PROMPT,
        OUTLINE_SYSTEM_PROMPT,
        PROJECTS_SECTION_SYSTEM_PROMPT,
        SKILLS_SECTION_SYSTEM_PROMPT,
        SUMMARY_SECTION_SYSTEM_PROMPT,
        WORK_SECTION_SYSTEM_PROMPT,
        build_education_prompt,
        build_outline_prompt,
        build_projects_prompt,
        build_skills_prompt,
        build_summary_prompt,
        build_work_section_prompt,
    )

    token_budget = SEGMENT_MAX_TOKENS

    # Reverse-chronological order is the orchestrator's policy (rule-2 parity), independent
    # of whatever the outline suggests — keeps the segmented path consistent with single-call.
    work_src: list[dict] = list(profile.get("work_experience") or [])
    if work_src:
        from applire.schemas.profile import WorkEntry
        we = [WorkEntry.model_validate(e) for e in work_src]
        work_src = [e.model_dump() for e in _sort_work_by_date(we)]
    for i, w in enumerate(work_src):
        w["id"] = w.get("id") or f"w{i}"

    directive = await provider.aparse_json(
        build_outline_prompt(job_analysis, profile, output_language, keyword_ledger, budget),
        system=OUTLINE_SYSTEM_PROMPT,
        temperature=0.3,
        max_tokens=token_budget,
    )

    work_entries: list[dict] = []
    for w in work_src:
        section = await provider.aparse_json(
            build_work_section_prompt(
                w, directive, job_analysis, keyword_gaps, output_language, keyword_ledger, budget
            ),
            system=WORK_SECTION_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=token_budget,
        )
        work_entries.append({
            "id": w["id"],
            "company": w.get("company", ""),
            "role": w.get("role", ""),
            "start_date": w.get("start_date") or "",
            "end_date": w.get("end_date"),
            "bullets": list(section.get("bullets") or []),
            "projects": list(section.get("projects") or []),
        })

    summary_res = await provider.aparse_json(
        build_summary_prompt(
            directive, job_analysis, profile, critical_gaps, output_language, keyword_ledger
        ),
        system=SUMMARY_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )
    skills_res = await provider.aparse_json(
        build_skills_prompt(
            directive, job_analysis, profile, keyword_gaps, output_language, keyword_ledger
        ),
        system=SKILLS_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )
    edu_res = await provider.aparse_json(
        build_education_prompt(profile, output_language),
        system=EDUCATION_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )
    projects_res = await provider.aparse_json(
        build_projects_prompt(directive, job_analysis, profile, output_language),
        system=PROJECTS_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )

    sections = {
        "contact": _contact_from_profile(profile),
        "summary": summary_res.get("summary") or "",
        "work_entries": work_entries,
        "skills": list(skills_res.get("skills") or []),
        "education": list(edu_res.get("education") or []),
        "languages": list(edu_res.get("languages") or []),
        "projects": list(projects_res.get("projects") or []),
    }
    # role_order = the deterministic reverse-chronological order (outline does not reorder).
    return assemble_segmented_cv({"role_order": [w["id"] for w in work_src]}, sections)


async def _should_segment_upfront() -> bool:
    """Skip the doomed single call when the model's known output cap sits below the
    single-call ceiling — the full CV won't fit, so go straight to segmented (ADR-047).

    The cap is resolved from the operator's declared ``LLM_MAX_OUTPUT_TOKENS`` OR, when
    undeclared, the US191 capability probe (ADR-047 §5) — so a capped OpenRouter/Ollama
    model is pre-empted even without operator config. An unknown cap (0) keeps the single
    call as the happy-path default; the reactive fallback (US189) covers it either way."""
    cap = await resolve_effective_output_cap()
    return 0 < cap < CV_GENERATION_MAX_TOKENS


async def _tailor_cv_with_fallback(
    job_analysis: dict,
    profile: dict,
    keyword_gaps: list[str],
    critical_gaps: list[str],
    *,
    output_language: str,
    provider: "LLMProvider",
    keyword_ledger: list[dict] | None = None,
    budget: "BudgetResult | None" = None,
) -> dict:
    """Produce the tailored CV draft: single call on the fast path, segmented as the
    fallback (ADR-047 §1/§2). On a known-small declared cap, segment upfront; otherwise try
    the single large call and switch to segmented on truncation/timeout rather than doubling
    the budget into a timeout (the US188 'switch to segmented' recovery). The returned draft
    is fed to the same coherence + language review as before by the caller.

    ``keyword_ledger`` (ADR-048 / US200) is surfaced into the prompt(s) as the
    claimable-vs-forbidden keyword split. ``budget`` (E042/US237, ADR-051 §3) is the
    deterministic per-role bullet-count ceiling table, threaded into whichever path runs."""
    if await _should_segment_upfront():
        return await generate_cv_segmented(
            job_analysis, profile, keyword_gaps, critical_gaps,
            output_language=output_language, provider=provider,
            keyword_ledger=keyword_ledger, budget=budget,
        )
    try:
        return await provider.aparse_json(
            build_user_prompt(
                job_analysis, profile, keyword_gaps, critical_gaps,
                output_language=output_language,
                keyword_ledger=keyword_ledger,
                budget=budget,
            ),
            system=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=CV_GENERATION_MAX_TOKENS,
        )
    except (LLMTruncatedError, LLMTimeoutError):
        logger.warning(
            "single-call CV tailoring hit the output cap/timeout; switching to segmented "
            "mode instead of doubling the budget (ADR-047)"
        )
        return await generate_cv_segmented(
            job_analysis, profile, keyword_gaps, critical_gaps,
            output_language=output_language, provider=provider,
            keyword_ledger=keyword_ledger, budget=budget,
        )


async def _review_cv_language(
    draft: dict, output_language: str, provider, keyword_ledger: list | None = None
) -> dict:
    """Enforce that the tailored CV's prose + skill tags are entirely in the target-job
    language (ADR-038), retrying via the ADR-021 review_and_refine loop. The tailoring
    directive alone leaks discipline-skill phrases; this is the enforcing pass — the same
    fix ADR-038 applied to interview questions. Never raises; no-op when the budget is 0.

    #122 follow-up: this chain runs AFTER the gated tailoring loop and rewrites wording,
    so it can silently translate a covered surface form into an unlisted synonym. The
    same US213 coverage wrapper feeds this reviewer; its remedy is word choice (use the
    exact required-language surface form), never inserting content.
    """
    if CV_LANGUAGE_REVIEW_MAX_RETRIES <= 0:
        return draft
    from applire.services.keyword_ledger import coverage_reviewer_prompt_fn

    return await review_and_refine(
        source=language_name(output_language),
        draft=draft,
        generator_prompt_fn=build_cv_language_refinement_prompt,
        generator_system=CV_LANGUAGE_REFINEMENT_PROMPT,
        reviewer_prompt_fn=coverage_reviewer_prompt_fn(
            build_cv_language_review_prompt, keyword_ledger
        ),
        reviewer_system=CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
        provider=provider,
        max_retries=CV_LANGUAGE_REVIEW_MAX_RETRIES,
        generator_max_tokens=CV_GENERATION_MAX_TOKENS,
        chain_id="cv_language",
    )

logger = logging.getLogger(__name__)


def _project_bullets(source_project: dict) -> list[str]:
    """Collapse a source ProjectEntry's responsibilities + achievements into the
    flat bullet list TailoredProjectEntry renders. Description leads when present
    so a one-line project still carries content. Order is stable and deduped."""
    bullets: list[str] = []
    desc = source_project.get("description")
    if isinstance(desc, str) and desc.strip():
        bullets.append(desc.strip())
    for key in ("responsibilities", "achievements"):
        for item in source_project.get(key) or []:
            if isinstance(item, str) and item.strip():
                bullets.append(item.strip())
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _nest_projects(tailored: TailoredCVData, profile_json: dict) -> TailoredCVData:
    """Deterministically place each source ProjectEntry under its parent position
    (US187). The LLM tailors prose but never carries project→parent identity; this
    code-side step is the truthful, testable disposer (ADR-044 / ADR-046 boundary).

    For each source project:
      * resolve its ``associated_experience`` to a source WorkEntry id;
      * locate the matching TailoredWorkEntry by company+role (the same stable
        identity the section editor uses) and nest the project there;
      * anything else (no parent, parent is a volunteer activity, or no tailored
        work entry matches) goes to the standalone top-level list so it is never
        dropped.

    Returns a new TailoredCVData; the input is left unmutated.
    """
    source_projects = profile_json.get("projects") or []
    if not source_projects:
        return tailored

    # Map source work id → company/role so we can find the tailored counterpart.
    # `associated_experience` is an id on the reconcile path (ADR-046) but a
    # company/organisation NAME on the CV-extraction path (prompts/cv_extraction.py),
    # so we index work entries by both id and company name.
    work_by_id: dict[str, dict] = {}
    work_by_company: dict[str, dict] = {}
    for w in profile_json.get("work_experience") or []:
        wid = w.get("id")
        if wid:
            work_by_id[str(wid)] = w
        company = (w.get("company") or "").strip().lower()
        if company:
            work_by_company.setdefault(company, w)

    data = tailored.model_dump()
    work_history = data.get("work_history") or []

    def _match_tailored_index(company: str, role: str) -> int | None:
        company_l = (company or "").strip().lower()
        role_l = (role or "").strip().lower()
        # Prefer an exact company+role match, then fall back to company-only.
        for idx, w in enumerate(work_history):
            if (w.get("company") or "").strip().lower() == company_l and (
                w.get("role") or ""
            ).strip().lower() == role_l:
                return idx
        for idx, w in enumerate(work_history):
            if company_l and (w.get("company") or "").strip().lower() == company_l:
                return idx
        return None

    standalone: list[dict] = []
    for proj in source_projects:
        name = (proj.get("name") or "").strip()
        if not name:
            continue
        entry = TailoredProjectEntry(name=name, bullets=_project_bullets(proj)).model_dump()

        parent_ref = proj.get("associated_experience")
        target_idx: int | None = None
        if parent_ref is not None:
            parent_key = str(parent_ref).strip()
            parent_work = work_by_id.get(parent_key) or work_by_company.get(
                parent_key.lower()
            )
            if parent_work is not None:
                target_idx = _match_tailored_index(
                    parent_work.get("company", ""), parent_work.get("role", "")
                )

        if target_idx is not None:
            work_history[target_idx].setdefault("projects", []).append(entry)
        else:
            standalone.append(entry)

    _suppress_duplicate_project_bullets(work_history)

    data["work_history"] = work_history
    data["projects"] = (data.get("projects") or []) + standalone
    return TailoredCVData.model_validate(data)


def _suppress_duplicate_project_bullets(work_history: list[dict]) -> None:
    """#169: the LLM often emits the same sentence twice — once as a role bullet and
    once inside the project nested under that role (the segmented per-entry writer
    emits ``bullets`` and ``projects`` in one JSON, so overlap is structural). Drop
    each nested-project bullet whose normalized form equals any of the PARENT role's
    own bullets. Deterministic; reuses ``ats_audit._norm`` (NFKC + dash→space +
    casefold) so "Code-Review" ≡ "code review". The project entry is kept even when
    all its bullets are suppressed (US187: the heading still carries the project).
    Mutates ``work_history`` in place; standalone projects are never touched.
    """
    from applire.services.ats_audit import _norm

    for w in work_history:
        role_norms = {
            _norm(b) for b in (w.get("bullets") or []) if isinstance(b, str) and b.strip()
        }
        if not role_norms:
            continue
        for proj in w.get("projects") or []:
            proj["bullets"] = [
                b
                for b in (proj.get("bullets") or [])
                if not (isinstance(b, str) and _norm(b) in role_norms)
            ]


def _apply_certifications(tailored: TailoredCVData, profile_json: dict) -> TailoredCVData:
    """Deterministically copy the Master Profile's certifications verbatim into
    ``tailored.certifications`` (PQ F7 / ADR-040 truthfulness).

    Certifications are FACTUAL data, like contact info — never routed through an
    LLM JSON schema. This is a pure passthrough (no selection, no LLM, no I/O),
    called after the LLM tailoring step(s) in both the single-call and segmented
    generation paths, mirroring ``_nest_projects``. Returns a new TailoredCVData;
    the input is left unmutated.
    """
    source_certs = profile_json.get("certifications") or []
    if not source_certs:
        return tailored
    return tailored.model_copy(
        update={"certifications": [TailoredCertification.model_validate(c) for c in source_certs]}
    )


def _enforce_work_order(tailored: TailoredCVData) -> TailoredCVData:
    """Deterministically re-sort ``tailored.work_history`` reverse-chronologically
    by START date (#118) — newest first; ties break on end date (open end =
    ongoing = 9999-12), then original order; missing/unparseable starts sort last.

    The LLM's ordering is advisory only: with two concurrent open-ended
    ("present") positions the end-date-only sort tied and the incidental profile
    order leaked into the rendered CV. Called once, after the LLM step(s) and the
    deterministic passthroughs, at the single site where ``tailored_data`` and
    ``content_snapshot`` are established — everything downstream (render, section
    editor, ATS audit) inherits the order from there. Pure; input unmutated.
    """
    if len(tailored.work_history) < 2:
        return tailored
    return tailored.model_copy(
        update={"work_history": _sort_work_by_date(list(tailored.work_history))}
    )


def _backfill_work_ids(tailored: TailoredCVData, profile_json: dict) -> TailoredCVData:
    """Deterministically back-fill the profile ``WorkEntry.id`` onto tailored work
    entries whose ``id`` is empty (E042/US238 fix round).

    The single-call fast path's LLM schema omits ``id`` (and the ADR-038 language
    pass re-emits the JSON, so even carried ids can be dropped) — but the condense
    loop's budget lookup is keyed by profile ``WorkEntry.id``. Without this pass the
    DEFAULT generation path would never match a role, silently skip condensation,
    and report "condensed to the maximum" without having condensed anything.

    Identity rule, mirroring ``_nest_projects``: match by case-folded, stripped
    company+role; each profile id is assigned at most once. Entries left ambiguous
    (duplicate company+role pairs, e.g. a re-hire) or unmatched fall back to
    POSITIONAL pairing — sound because tailoring rule 6 guarantees the output entry
    count equals the profile's, and both lists are enforced reverse-chronological
    (``_sort_work_by_date`` upstream, ``_enforce_work_order`` on the tailored side —
    call this AFTER it). When the counts differ, unmatched entries keep an empty id
    (no budget applied) rather than risk a wrong role's budget. Pure; no LLM; the
    input is left unmutated.
    """
    source = profile_json.get("work_experience") or []
    if not source or not tailored.work_history:
        return tailored
    if all(w.id for w in tailored.work_history):
        return tailored  # segmented path (or already back-filled) — nothing to do

    def _key(company: object, role: object) -> tuple[str, str]:
        return (
            (company if isinstance(company, str) else "").strip().lower(),
            (role if isinstance(role, str) else "").strip().lower(),
        )

    ids_by_key: dict[tuple[str, str], list[str]] = {}
    for s in source:
        sid = str(s.get("id") or "")
        if sid:
            ids_by_key.setdefault(_key(s.get("company"), s.get("role")), []).append(sid)

    data = tailored.model_dump()
    work: list[dict] = data.get("work_history") or []
    used: set[str] = {w["id"] for w in work if w.get("id")}

    unmatched: list[int] = []
    for i, w in enumerate(work):
        if w.get("id"):
            continue
        candidates = [
            sid for sid in ids_by_key.get(_key(w.get("company"), w.get("role")), [])
            if sid not in used
        ]
        if len(candidates) == 1:
            w["id"] = candidates[0]
            used.add(candidates[0])
        else:
            unmatched.append(i)

    # Positional fallback (documented above): only when the counts line up 1:1.
    if unmatched and len(work) == len(source):
        for i in unmatched:
            sid = str(source[i].get("id") or "")
            if sid and sid not in used:
                work[i]["id"] = sid
                used.add(sid)

    return TailoredCVData.model_validate(data)


def _cap_bullets(
    bullets: list[str], is_hit: Callable[[str], bool], max_bullets: int
) -> list[str]:
    """Trim ``bullets`` down to ``max_bullets``, mirroring
    ``cv_budget.condense_to_budget``'s cut order: no-hit bullets are removed
    before hit bullets, and within an equal hit-status the later-listed bullet
    is removed first (so the earliest, typically strongest, bullets survive).
    A hit bullet is NEVER removed while a no-hit bullet remains.

    Unlike the restore-path reordering in ``_restore_ledger_bullets`` (which
    intentionally regroups hits-first), this preserves the SURVIVORS' original
    relative order -- entries this pass didn't otherwise touch must not have
    their bullet order perturbed, only cut down to size.

    No-op (returns ``bullets`` unchanged, same object) when already within budget.
    """
    if len(bullets) <= max_bullets:
        return bullets
    indexed = [(i, b, is_hit(b)) for i, b in enumerate(bullets)]
    # Ascending (has_hit, -order): no-hit (False) sorts before hit (True); within
    # a tie, the higher (later) order sorts first -- i.e. later-listed first.
    removal_order = sorted(indexed, key=lambda t: (t[2], -t[0]))
    cut = len(bullets) - max_bullets
    removed_idx = {t[0] for t in removal_order[:cut]}
    return [b for i, b, _hit in indexed if i not in removed_idx]


def _restore_ledger_bullets(
    tailored: TailoredCVData,
    profile_json: dict,
    keyword_ledger: list[dict] | None,
    budget: "BudgetResult | None",
) -> TailoredCVData:
    """#234 (Tiramisu founder-acceptance F1/F2) — deterministic post-draft guard.

    Ground truth: a vault work entry had 9 responsibilities (5 interview-elicited,
    mapping 1:1 to JD requirements); the tailored CV kept only the 4 generic
    LinkedIn-baseline bullets the LLM writer preferred. The Keyword Ledger prompt
    block (ADR-048) only ever surfaces a GENERIC claimable-terms list — nothing
    guarantees any specific vault bullet survives the writer's own selection, and
    the bounded coverage-review loop (#122) can only ask the writer to reword a
    *sentence*, never restore a whole dropped bullet.

    Uses THE shared presence predicate (``ats_audit.surface_present`` via
    ``keyword_ledger.verified_missing_claimable`` — #122: "the loop that grades is
    the loop that heals") to find claimable concepts verifiably ABSENT from the
    whole tailored document, then restores — VERBATIM, never rephrased — any vault
    responsibility/achievement bullet of the OWNING work entry that carries one of
    them. Restoration happens within that entry's ``RoleBudget.max_bullets``
    ceiling (E042/US237, ADR-051 §3): generic no-hit tailored bullets yield first
    when the ceiling is tight, and every entry's final bullet order is put
    ledger-hit-first — the same rule a later page-overrun ``condense_to_budget``
    pass already uses (no-hit + later-listed cut first), so evidence never gets
    cut before filler.

    A concept is restored at most once (into the first entry — reverse-
    chronological order — whose vault text carries it), so a second pass over an
    already-restored document is a no-op. Pure; ``tailored`` is left unmutated.

    Ceiling enforcement is unconditional, not restoration-conditional (friction
    finding adjacent to #234): EVERY work entry is capped at its
    ``RoleBudget.max_bullets`` even when this pass restored nothing into it, since
    upstream (the writer, or the #122 coverage-review loop asking for a reworded
    sentence) has no ceiling awareness of its own and can leave an entry over
    budget with no restoration ever happening. No-hit bullets are cut before hit
    bullets, later-listed before earlier — mirrors ``cv_budget.condense_to_budget``'s
    cut order exactly. Entries already within budget keep their original bullets
    AND order untouched.
    """
    if not keyword_ledger:
        return tailored

    from applire.services.ats_audit import _norm, surface_present
    from applire.services.keyword_ledger import verified_missing_claimable

    # NOTE: deliberately no early return when ``missing`` is empty — an entry can
    # still be over its RoleBudget ceiling with nothing left to restore (the #122
    # coverage-review loop pushing an ADD with no ceiling awareness of its own),
    # and the per-entry loop below must run to enforce that ceiling regardless.
    missing = verified_missing_claimable(tailored.model_dump(mode="json"), keyword_ledger)

    # Vault entries keyed by id — the SAME identity ``_backfill_work_ids`` relies on;
    # this guard MUST run after it so tailored ids are populated.
    vault_by_id: dict[str, dict] = {}
    for w in profile_json.get("work_experience") or []:
        wid = str(w.get("id") or "")
        if wid:
            vault_by_id[wid] = w

    def _entry_forms(entry: dict) -> list[str]:
        forms = list(entry.get("surface_forms") or [])
        if entry.get("concept"):
            forms.append(entry["concept"])
        return forms

    claimable_forms: tuple[str, ...] = budget.claimable_forms if budget is not None else ()

    def _is_hit(text: str) -> bool:
        if not claimable_forms:
            return False
        n = _norm(text)
        return bool(n) and any(surface_present(f, n) for f in claimable_forms)

    remaining = list(missing)  # concepts still unrestored; consumed as entries claim them
    changed = False
    new_work: list[dict] = []

    for w in tailored.work_history:
        w_dict = w.model_dump(mode="json")
        eid = str(w_dict.get("id") or "")
        existing_bullets = [b for b in (w_dict.get("bullets") or []) if isinstance(b, str)]
        existing_norms = {_norm(b) for b in existing_bullets}

        vault_entry = vault_by_id.get(eid)
        restored: list[str] = []
        if vault_entry is not None and remaining:
            vault_bullets = [
                b for key in ("responsibilities", "achievements")
                for b in (vault_entry.get(key) or [])
                if isinstance(b, str) and b.strip()
            ]
            for vb in vault_bullets:
                vb_norm = _norm(vb)
                if not vb_norm or vb_norm in existing_norms:
                    continue
                hit_idx = next(
                    (i for i, m in enumerate(remaining)
                     if any(surface_present(f, vb_norm) for f in _entry_forms(m))),
                    None,
                )
                if hit_idx is None:
                    continue
                restored.append(vb)
                existing_norms.add(vb_norm)
                remaining.pop(hit_idx)

        rb = budget.roles.get(eid) if budget is not None else None

        if restored:
            changed = True
            hits = [b for b in existing_bullets if _is_hit(b)] + restored
            no_hits = [b for b in existing_bullets if not _is_hit(b)]
            ordered = hits + no_hits
            if rb is not None and len(ordered) > rb.max_bullets:
                ordered = ordered[: rb.max_bullets]
            w_dict["bullets"] = ordered
            new_work.append(w_dict)
            continue

        # Nothing to restore into this entry, but #234-adjacent friction finding:
        # upstream passes (the writer, and the #122 coverage-review loop) have no
        # ceiling awareness of their own -- a review-driven ADD can leave an entry
        # over its RoleBudget with nothing downstream to trim it back except a
        # page-overrun condense pass that may never fire. Enforce the ceiling
        # deterministically here too, mirroring cv_budget.condense_to_budget's cut
        # order (no-hit bullets first, later-listed first within a tie -- a hit
        # bullet is only ever cut once every no-hit bullet is gone). Untouched
        # (under-ceiling) entries keep their original bullets AND order exactly.
        if rb is not None and len(existing_bullets) > rb.max_bullets:
            capped = _cap_bullets(existing_bullets, _is_hit, rb.max_bullets)
            if capped != existing_bullets:
                changed = True
                w_dict["bullets"] = capped

        new_work.append(w_dict)

    if not changed:
        return tailored
    return tailored.model_copy(
        update={"work_history": [TailoredWorkEntry.model_validate(w) for w in new_work]}
    )


def _dedup_skills(tailored: TailoredCVData) -> TailoredCVData:
    """#172: collapse near-duplicate skill tags so the rendered CV stays clean even
    when the master profile is still dirty (the reconciler merges going forward, but
    existing profiles carry twins like 'Team Leadership' + 'Team Leadership and
    Mentorship'). Uses the SAME shared predicate as the reconciler and the audit.

    Keeps the first-seen occurrence's POSITION (stable order) but upgrades its name
    to the more-specific variant when a later near-dupe strictly contains it. Pure;
    input unmutated. Must run AFTER the ADR-038 language pass, which rewords tags.
    """
    from applire.services.ats_audit import skill_tokens, skills_near_dupe

    original = list(tailored.skills or [])
    kept: list[str] = []
    for s in original:
        dup_idx = next(
            (i for i, k in enumerate(kept) if skills_near_dupe(k, s)), None
        )
        if dup_idx is None:
            kept.append(s)
        elif skill_tokens(s) > skill_tokens(kept[dup_idx]):
            kept[dup_idx] = s  # upgrade in place to the more-specific name
    if kept == original:
        return tailored
    return tailored.model_copy(update={"skills": kept})


def _jd_skill_terms(
    job_dict: dict, keyword_ledger: list[dict] | None
) -> tuple[list[str], list[str], list[str]]:
    """Collect the JD's skill expectations bucketed by priority tier (#192).

    Draws from BOTH the JobAnalysis fields (``required_skills`` / ``nice_to_have_skills``
    / ``keywords``) and the Keyword Ledger (ADR-048): each ledger entry's ``concept`` +
    ``surface_forms`` are filed by its highest-priority ``sources`` tag. Returns
    ``(required, nice_to_have, keyword)`` term lists — the ranking signal for skill
    selection, never a source of new skill *names* (those only ever come from the profile).
    """
    def _strs(seq: object) -> list[str]:
        return [s for s in (seq or []) if isinstance(s, str) and s.strip()]

    required = _strs(job_dict.get("required_skills"))
    nice = _strs(job_dict.get("nice_to_have_skills"))
    keyword = _strs(job_dict.get("keywords"))
    for entry in keyword_ledger or []:
        forms = _strs([entry.get("concept")]) + _strs(entry.get("surface_forms"))
        sources = entry.get("sources") or []
        if "required" in sources:
            required += forms
        elif "nice_to_have" in sources:
            nice += forms
        else:
            keyword += forms
    return required, nice, keyword


def _tailor_skills_to_jd(
    tailored: TailoredCVData,
    profile_json: dict,
    job_dict: dict,
    keyword_ledger: list[dict] | None,
    *,
    cap: int = CV_MAX_SKILLS,
) -> TailoredCVData:
    """#192: present a prioritised, JD-relevant SUBSET of the candidate's skills.

    The LLM path tends to echo nearly the whole master profile (~70 tags) into the
    skills section: it keeps clearly-irrelevant tags (regulated-pharma skills on a SaaS
    CTO CV) AND sometimes drops JD-required skills the candidate actually has. This
    deterministic pass fixes both, downstream of the LLM, so it holds for every provider:

    * **Rank** every candidate tag by relevance — required (tier 0) > nice-to-have (1) >
      keyword (2) > no JD relevance (3) — matched on the shared normalised token sets
      (:func:`skill_tokens`), so 'React' matches JD 'React' but pharma 'CAPA management'
      matches nothing on an AI-SaaS JD.
    * **Guarantee** required ∩ profile: a JD-required skill present in the master profile
      but dropped by the writer is re-added from the profile's own spelling (never
      invented), and tier-0 tags are kept even past the cap.
    * **Cap** the count at ``cap``; no-relevance tags (tier 3) are dropped FIRST when over
      it, so the pharma tags fall away while React/Node.js/Leadership stay.

    Truthful by construction: the candidate pool is the writer's tags PLUS profile skills
    only — nothing outside the master profile can enter. Pure; input unmutated.
    """
    from applire.services.ats_audit import (
        _NEAR_DUPE_JACCARD,
        skill_tokens,
        skills_near_dupe,
    )

    tailored_skills = [s for s in (tailored.skills or []) if isinstance(s, str) and s.strip()]
    # Master-profile skills are stored as objects ({"name": ..., "category": ...}), not bare
    # strings — the #192 guarantee below silently saw NONE of them when it filtered for `str`,
    # so JD-required skills the writer dropped (React/Node.js/JavaScript) were never re-added.
    # Extract the display name (dict → .name, or a plain string for legacy/mock data), keeping
    # the profile's own spelling verbatim — never fabricated. Mirrors gap_inference/choice_grounding.
    profile_skills: list[str] = []
    for s in profile_json.get("skills") or []:
        name = s.get("name") if isinstance(s, dict) else s
        if isinstance(name, str) and name.strip():
            profile_skills.append(name.strip())

    required, nice, keyword = _jd_skill_terms(job_dict, keyword_ledger)
    req_toks = [t for t in (skill_tokens(x) for x in required) if t]
    nice_toks = [t for t in (skill_tokens(x) for x in nice) if t]
    kw_toks = [t for t in (skill_tokens(x) for x in keyword) if t]

    def _relevant(st: frozenset[str], tt: frozenset[str]) -> bool:
        # A skill maps to a JD term on equality/containment (JD 'React' ⊆ 'React Native')
        # or strong token overlap — NOT a single shared common token ('MES systems' vs
        # 'System Architecture' share only 'system', Jaccard 0.33 < 0.75 → no match).
        if not st or not tt:
            return False
        if st <= tt or tt <= st:
            return True
        return len(st & tt) / len(st | tt) >= _NEAR_DUPE_JACCARD

    def _matches(skill_toks: frozenset[str], term_toks: list[frozenset[str]]) -> bool:
        return any(_relevant(skill_toks, t) for t in term_toks)

    def _tier(skill: str) -> int:
        st = skill_tokens(skill)
        if not st:
            return 3
        if _matches(st, req_toks):
            return 0
        if _matches(st, nice_toks):
            return 1
        if _matches(st, kw_toks):
            return 2
        return 3

    # Candidate pool = the writer's tags, PLUS any master-profile skill that maps to a
    # JD-required term but the writer dropped (defect #2). Profile spelling is used verbatim
    # — no fabrication. Order: writer's tags first (they carry the ADR-038 language pass),
    # re-added required skills appended.
    pool = list(tailored_skills)
    for p in profile_skills:
        if _tier(p) == 0 and not any(skills_near_dupe(p, x) for x in pool):
            pool.append(p)

    # Collapse near-dupes (the newly re-added profile skills may twin a writer tag), keeping
    # the more-specific name — same shared predicate as _dedup_skills.
    deduped: list[str] = []
    for s in pool:
        dup = next((i for i, k in enumerate(deduped) if skills_near_dupe(k, s)), None)
        if dup is None:
            deduped.append(s)
        elif skill_tokens(s) > skill_tokens(deduped[dup]):
            deduped[dup] = s

    # Stable sort by tier (required lead the section); keep all tier-0 even past the cap,
    # then fill remaining slots up to `cap` in tier order — tier-3 (no relevance) drops first.
    ranked = sorted(enumerate(deduped), key=lambda it: (_tier(it[1]), it[0]))
    selected: list[str] = []
    for _, s in ranked:
        if _tier(s) == 0 or len(selected) < cap:
            selected.append(s)

    if selected == tailored_skills:
        return tailored
    return tailored.model_copy(update={"skills": selected})


def _drop_ungrounded_jd_echo_skills(
    tailored: TailoredCVData,
    profile_json: dict,
    job_dict: dict,
    keyword_ledger: list[dict] | None,
) -> TailoredCVData:
    """#250 (Tiramisu founder-acceptance blind-panel finding, run 3, 2026-07-24).

    Ground truth: the tailored CV's Skills section carried near-verbatim JD phrases
    as bare skill tags ("Fast-Moving Product-Led Environment Experience", "Commercial
    AI Product Development", "AI Reliability", "AI Observability", "Production
    Ownership"). Both blind reviewers (HR + hiring manager) independently called this
    keyword-stuffing / inflation, even though the truthfulness pipeline never flags
    it -- every one of those concepts is ledger-CLAIMABLE, so grounding never trips.
    The defect is PLACEMENT, not truthfulness: a human reader reads a JD phrase minted
    as a skill tag as inflation, because a skill tag with no attested vault backing
    carries none of the evidence a work bullet or the summary would.

    Drops a tailored skill entry only when BOTH hold:

    * it near-dupes NO master-profile skill (:func:`ats_audit.skills_near_dupe` --
      the SAME shared containment/near-dupe instrument #172/#192/#244 already use,
      so this pass can never disagree with the dedup/tailoring passes about what
      counts as "the same skill"), AND
    * it near-dupes a JD-required/nice-to-have/keyword term or a Keyword Ledger
      concept/surface form (:func:`_jd_skill_terms`) -- i.e. it reads as an echo of
      the posting's own phrasing, not an independently-attested candidate skill.

    A skill that DOES near-dupe a vault skill is NEVER dropped -- and is reworded to
    the vault's own phrasing when it currently reads as the JD's rather than the
    vault's (prefer the attested name over the posting's), so "Team Leadership" (the
    writer's JD-flavoured trim of the vault's "Team Leadership and Mentorship")
    surfaces under its real, vault-grounded name rather than vanishing or staying in
    the JD's words. A concept that ALSO exists as a genuine vault skill (e.g. "AI
    Observability" with real ``experience_refs``) is correctly kept even though it
    happens to match the JD's own phrasing verbatim -- the fix is JD-echo-with-no-tie,
    never "matches the JD" alone.

    Runs LAST in the skills pipeline (after #192's ``_tailor_skills_to_jd``), so a
    dropped tag can never be silently re-added by an earlier pass, and strictly
    BEFORE the record's ``tailored_data``/ATS audit are persisted -- so
    ``keyword_ledger.verified_missing_claimable`` (US213/#122's shared presence
    predicate) sees the true, final document: a concept still genuinely present in a
    bullet/summary elsewhere stays covered (no false amber), while a concept that was
    ONLY ever covered by the now-dropped tag honestly reappears as missing-claimable
    instead of being silently laundered by a bare tag.

    Pure; ``tailored`` is left unmutated. No-op (returns ``tailored`` unchanged, same
    object) when nothing is dropped or renamed.
    """
    from applire.services.ats_audit import skills_near_dupe

    original = [s for s in (tailored.skills or []) if isinstance(s, str) and s.strip()]
    if not original:
        return tailored

    profile_skills: list[str] = []
    for s in profile_json.get("skills") or []:
        name = s.get("name") if isinstance(s, dict) else s
        if isinstance(name, str) and name.strip():
            profile_skills.append(name.strip())

    required, nice, keyword = _jd_skill_terms(job_dict, keyword_ledger)
    jd_terms = required + nice + keyword

    def _vault_tie(skill: str) -> str | None:
        """The vault skill's own name this tag near-dupes, if any (first match)."""
        return next((p for p in profile_skills if skills_near_dupe(skill, p)), None)

    def _is_jd_echo(skill: str) -> bool:
        return any(skills_near_dupe(skill, t) for t in jd_terms)

    kept: list[str] = []
    for s in original:
        tie = _vault_tie(s)
        if tie is None:
            if _is_jd_echo(s):
                continue  # bare JD echo, no deterministic vault tie -- drop
            kept.append(s)
            continue
        # Vault-tied: always survives. Prefer the vault's own attested phrasing over
        # the JD's when the tag currently reads as a JD echo of it.
        name = tie if (s != tie and _is_jd_echo(s)) else s
        if not any(skills_near_dupe(name, k) for k in kept):
            kept.append(name)
        # else: collapses into an already-kept near-dupe (dedup safety net --
        # renaming toward the vault name must never re-introduce a duplicate).

    if kept == original:
        return tailored
    return tailored.model_copy(update={"skills": kept})


_TEMPLATE_FILES: dict[str, str] = {
    "classic_german": "lebenslauf.html.j2",
    "modern_swiss": "modern_swiss.html.j2",
    "executive": "executive.html.j2",
    "tech_developer": "tech_developer.html.j2",
    "creative_sidebar": "creative_sidebar.html.j2",
    "academic": "academic.html.j2",
    "compact_pro": "compact_pro.html.j2",
}

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

_PHOTO_MIME: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


async def _resolve_photo_data_uri(
    photo_path: str | None,
    storage: "StorageProvider",
) -> str | None:
    """Convert a stored file path to an inline base64 data URI.

    Returns None if photo_path is None or the file has been deleted.
    The data URI is safe to embed in Jinja2 templates served via Playwright or srcDoc.
    """
    if not photo_path:
        return None
    try:
        photo_bytes = await storage.read(photo_path)
    except FileNotFoundError:
        return None
    suffix = Path(photo_path).suffix.lower().lstrip(".")
    mime = _PHOTO_MIME.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{_base64.b64encode(photo_bytes).decode()}"


# ---------------------------------------------------------------------------
# POST /api/cv/generate — enqueue and return immediately
# ---------------------------------------------------------------------------


async def generate_cv(
    job_id: uuid.UUID,
    db: AsyncSession,
    provider: LLMProvider,
    background_tasks: BackgroundTasks | None = None,
    template: CVTemplate = "classic_german",
    base_url: str = "http://localhost:8001",
    target_pages: int | None = None,
) -> CVGenerateResponse:
    """Create a GeneratedCV record and render it.

    REST passes a ``BackgroundTasks`` so rendering runs after the response is
    sent. The MCP/agent channel has no request lifecycle, so it omits it
    (``background_tasks=None``) and we render inline before returning — the agent
    polls ``get_cv_status`` and sees a terminal status on the first read.

    ``target_pages`` (E042/US236, ADR-051 §1) is the optional per-generation
    override. Precedence — resolved once here and persisted on the row —
    is override > the user's ``UserSettings.target_cv_pages`` > the DACH
    region standard (``resolve_target_pages``).
    """
    # Validate job exists
    job = await db.get(JobAnalysis, job_id)
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")

    # Validate profile exists
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError("No profile found — import a CV first")

    from applire.models.user_settings import UserSettings
    from applire.services.color_detection import _CE_STUB_USER_ID

    settings_result = await db.execute(
        select(UserSettings.target_cv_pages).where(
            UserSettings.user_id == _CE_STUB_USER_ID
        )
    )
    user_setting = settings_result.scalar_one_or_none()
    resolved_target_pages = resolve_target_pages(target_pages, user_setting)

    # Create pending record
    record = GeneratedCV(
        job_analysis_id=job_id,
        profile_id=profile.id,
        tailored_data={},  # populated by background task
        template=template,
        status=CVGenerationStatus.pending.value,
        target_pages=resolved_target_pages,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    if background_tasks is None:
        # Agent channel: no request lifecycle to defer to — render inline.
        await _render_cv_background(record.id, job_id, profile.id, template)
        await db.refresh(record)
    else:
        # REST: enqueue heavy work — runs after the response is sent.
        background_tasks.add_task(
            _render_cv_background,
            record.id,
            job_id,
            profile.id,
            template,
        )

    return CVGenerateResponse(
        cv_id=record.id,
        status=CVGenerationStatus(record.status),
        html_url=f"{base_url}/api/cv/{record.id}/html",
        pdf_url=f"{base_url}/api/cv/{record.id}/pdf",
        expires_at=record.expires_at,
    )


# ---------------------------------------------------------------------------
# GET /api/cv/{cv_id}/status
# ---------------------------------------------------------------------------


async def get_cv_status(
    cv_id: uuid.UUID,
    db: AsyncSession,
    base_url: str,
) -> CVStatusResponse:
    from datetime import timedelta
    from datetime import datetime as _dt

    record = await _load_cv(cv_id, db)
    status = CVGenerationStatus(record.status)

    # Inline staleness check: give the frontend immediate failed feedback without
    # waiting for the daily Retention Worker run. The worker still cleans up the
    # DB record; this is belt-and-suspenders for the polling path.
    _STALE_MINUTES = 10
    if status in (CVGenerationStatus.pending, CVGenerationStatus.generating):
        cutoff = _dt.now(timezone.utc) - timedelta(minutes=_STALE_MINUTES)
        if record.created_at < cutoff:
            status = CVGenerationStatus.failed

    return CVStatusResponse(
        cv_id=record.id,
        status=status,
        html_url=f"{base_url}/api/cv/{cv_id}/html" if status == CVGenerationStatus.ready else None,
        pdf_url=f"{base_url}/api/cv/{cv_id}/pdf" if status == CVGenerationStatus.ready else None,
        # Surface only the machine code; the frontend localizes it. Raw error_message stays
        # internal (ADR-047 §4 / PQ F6). Fall back to a generic code for legacy failed rows.
        error_code=(
            record.error_code or ("generation_failed" if status == CVGenerationStatus.failed else None)
        ),
        expires_at=record.expires_at,
        target_pages=record.target_pages,
        origin=record.origin,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/cvs — list all CVs for a job
# ---------------------------------------------------------------------------


async def list_cvs_for_job(
    job_id: uuid.UUID,
    db: AsyncSession,
    base_url: str,
) -> list[CVStatusResponse]:
    """Return all non-deleted CVs for a job, newest first."""
    result = await db.execute(
        select(GeneratedCV)
        .where(
            GeneratedCV.job_analysis_id == job_id,
            GeneratedCV.deleted_at.is_(None),
        )
        .order_by(GeneratedCV.created_at.desc())
    )
    records = result.scalars().all()
    return [
        CVStatusResponse(
            cv_id=r.id,
            status=CVGenerationStatus(r.status),
            html_url=f"{base_url}/api/cv/{r.id}/html" if r.status == CVGenerationStatus.ready.value else None,
            pdf_url=f"{base_url}/api/cv/{r.id}/pdf" if r.status == CVGenerationStatus.ready.value else None,
            # Machine code only; raw error_message stays internal (ADR-047 §4 / PQ F6).
            error_code=(
                r.error_code or ("generation_failed" if r.status == CVGenerationStatus.failed.value else None)
            ),
            expires_at=r.expires_at,
            template=r.template,
            created_at=r.created_at,
            target_pages=r.target_pages,
            origin=r.origin,
        )
        for r in records
    ]


# ---------------------------------------------------------------------------
# PDF filename helper
# ---------------------------------------------------------------------------


def filename_part(value: str | None) -> str:
    """Sanitize one segment of a download filename (E039/US219, FMEA JF-E-Q.1).

    Umlaut-safe (ä→ae, ß→ss per DIN 5007-2) and diacritic-safe (á→a, č→c via
    NFKD fold — a name must never lose letters), whitespace→hyphen, everything
    else outside [A-Za-z0-9-] dropped. Case is preserved so the file reads like
    a document title in a Downloads folder, not a URL slug.
    """
    if not value:
        return ""
    # German umlauts first — the two-letter forms are the expected DACH spelling
    # and would be lost to a plain base-letter fold (ü→u, not ue).
    for src, dst in (
        ("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
        ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"),
        ("ß", "ss"),
    ):
        value = value.replace(src, dst)
    # Fold remaining diacritics to their base letter (á→a, é→e, č→c) instead of
    # dropping them — "Milan Novák" must become "Milan-Novak", never "Milan-Novk".
    value = "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )
    value = re.sub(r"[\s_]+", "-", value.strip())
    value = re.sub(r"[^A-Za-z0-9-]", "", value)
    return re.sub(r"-+", "-", value).strip("-")


def compose_document_filename(
    *parts: str | None, suffix: str = "", fallback: str
) -> str:
    """Join sanitized parts as <name>_<company>_<role>[_suffix].pdf; empty parts
    are skipped. When nothing survives sanitization, fall back to a stable id-
    based name so the header never carries an empty filename."""
    clean = [p for p in (filename_part(part) for part in parts) if p]
    if not clean:
        return f"{fallback}.pdf"
    if suffix:
        clean.append(suffix)
    return "_".join(clean) + ".pdf"


async def get_pdf_filename(cv_id: uuid.UUID, db: AsyncSession) -> str:
    """Build the Content-Disposition filename for a CV PDF.

    Format: <name>_<company>_<role>.pdf (sanitized, umlaut-safe) — the download
    must stay identifiable among a pipeline's worth of files (E039/US219).
    """
    record = await _load_cv_ready(cv_id, db)
    job = await db.get(JobAnalysis, record.job_analysis_id)
    contact = (record.tailored_data or {}).get("contact") or {}
    return compose_document_filename(
        contact.get("name"),
        job.company_name if job else None,
        job.role_title if job else None,
        fallback=f"lebenslauf-{str(cv_id)[:8]}",
    )


# ---------------------------------------------------------------------------
# GET /api/cv/{cv_id}/html  (requires status=ready)
# ---------------------------------------------------------------------------


async def get_cv_html(cv_id: uuid.UUID, db: AsyncSession) -> str:
    from applire.services.cv_section_editor import apply_overrides_to_tailored
    from applire.storage import get_storage

    record = await _load_cv_ready(cv_id, db)
    tailored = TailoredCVData.model_validate(record.tailored_data)
    tailored = apply_overrides_to_tailored(
        tailored, record.content_snapshot, record.section_overrides
    )

    # Resolve stored photo path → inline base64 data URI for Playwright / srcDoc.
    # If the file is missing (deleted after CV was generated) the photo is silently omitted.
    if tailored.show_photo and tailored.contact.photo_url:
        data_uri = await _resolve_photo_data_uri(tailored.contact.photo_url, get_storage())
        if data_uri is not None:
            tailored = tailored.model_copy(update={
                "contact": tailored.contact.model_copy(update={"photo_url": data_uri})
            })

    from applire.services.color_detection import resolve_color_context
    color_ctx = await resolve_color_context(record, db)

    template_file = _TEMPLATE_FILES.get(record.template, "lebenslauf.html.j2")
    template = _jinja_env.get_template(template_file)
    # #4 (ADR-038): section headings follow the document's output language, resolved
    # from the target job. Injected as `labels`/`lang` so template chrome matches content.
    job = await db.get(JobAnalysis, record.job_analysis_id)
    lang = resolve_jd_language(job) if job else "de"
    return template.render(cv=tailored, color=color_ctx, lang=lang, labels=cv_labels(lang))


# ---------------------------------------------------------------------------
# GET /api/cv/{cv_id}/pdf  (requires status=ready)
# ---------------------------------------------------------------------------


async def get_cv_pdf(cv_id: uuid.UUID, db: AsyncSession) -> bytes:
    html = await get_cv_html(cv_id, db)
    return await _html_to_pdf(html)


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def _render_cv_background(
    cv_id: uuid.UUID,
    job_id: uuid.UUID,
    profile_id: uuid.UUID,
    template: CVTemplate,
) -> None:
    """LLM tailoring + Playwright PDF rendering — runs outside request lifecycle.

    Opens its own DB session. Updates status: pending → generating → ready | failed.
    """
    async with AsyncSessionLocal() as db:
        record = await db.get(GeneratedCV, cv_id)
        if record is None:
            logger.error("CV %s not found in background task", cv_id)
            return

        try:
            record.status = CVGenerationStatus.generating.value
            await db.commit()

            # Load job + profile + optional gap analysis
            job = await db.get(JobAnalysis, job_id)
            profile = await db.get(MasterProfile, profile_id)

            # Auto-detect and cache company brand color (best-effort; never blocks CV generation)
            try:
                from applire.services.color_detection import detect_and_cache_company_color
                await detect_and_cache_company_color(job, db)
            except Exception:
                logger.debug("detect_and_cache_company_color failed silently", exc_info=True)

            gap_result = await db.execute(
                select(GapAnalysis)
                .where(
                    GapAnalysis.job_analysis_id == job_id,
                    GapAnalysis.deleted_at.is_(None),
                )
                .order_by(GapAnalysis.created_at.desc())
                .limit(1)
            )
            gap = gap_result.scalar_one_or_none()
            keyword_gaps: list[str] = gap.keyword_gaps if gap else []
            critical_gaps: list[str] = gap.critical_gaps if gap else []
            # ADR-048 / US200: the Keyword Ledger drives claimable-vs-forbidden keyword
            # surfacing in the tailoring prompt (legacy pre-E037 gap rows have none).
            keyword_ledger: list[dict] = (gap.keyword_ledger or []) if gap else []

            job_dict = {
                "role_title": job.role_title,
                "required_skills": job.required_skills,
                "nice_to_have_skills": job.nice_to_have_skills,
                "keywords": job.keywords,
                "seniority_level": job.seniority_level,
                "company_culture_signals": job.company_culture_signals,
                "language_requirement": job.language_requirement,
            }

            # Sort work experience reverse-chronologically before passing to LLM.
            # Handles profiles that pre-date the merge-time sort.
            profile_json: dict = dict(profile.profile_json or {})
            if profile_json.get("work_experience"):
                from applire.schemas.profile import WorkEntry
                we = [WorkEntry.model_validate(e) for e in profile_json["work_experience"]]
                profile_json["work_experience"] = [
                    e.model_dump() for e in _sort_work_by_date(we)
                ]

            # E042/US237 (ADR-051 §3): compute the deterministic per-role bullet budget
            # BEFORE generation, from the profile + Keyword Ledger + this row's resolved
            # target_pages (Task 1.1 persists it non-NULL for every new row; the fallback
            # here only guards a pre-E042 legacy record). Threaded into both LLM paths
            # below so the model aims at the target page count directly.
            from applire.services.cv_budget import attach_projects, compute_bullet_budgets

            resolved_target_pages = (
                record.target_pages
                if record.target_pages is not None
                else resolve_target_pages(None, None)
            )
            budget_work_entries = attach_projects(
                profile_json.get("work_experience") or [], profile_json.get("projects") or []
            )
            budget = compute_bullet_budgets(
                budget_work_entries, keyword_ledger, resolved_target_pages
            )

            provider: LLMProvider = get_provider()
            # Single call on the fast path; segmented (outline-then-expand) as the fallback
            # on truncation/timeout or a known-small cap (ADR-047 §1/§2 / US189).
            tailored_raw: dict = await _tailor_cv_with_fallback(
                job_dict,
                profile_json,
                keyword_gaps,
                critical_gaps,
                output_language=resolve_jd_language(job),
                provider=provider,
                keyword_ledger=keyword_ledger,
                budget=budget,
            )

            source_material = _json.dumps(profile_json, ensure_ascii=False, indent=2)
            # ADR-048 / US202+US213 (#122): route the Keyword Ledger to the reviewer for the
            # forbidden-claim check, and wrap the reviewer prompt so each iteration carries
            # the DETERMINISTIC verified-coverage state of the current draft (the LLM no
            # longer detects absent claimable terms — it only arbitrates grounding waivers).
            from applire.services.keyword_ledger import (
                coverage_reviewer_prompt_fn,
                render_ledger_reviewer_block,
            )
            ledger_block = render_ledger_reviewer_block(keyword_ledger)
            if ledger_block:
                source_material = f"{source_material}\n\n{ledger_block}"

            tailored_raw = await review_and_refine(
                source=source_material,
                draft=tailored_raw,
                generator_prompt_fn=_build_cv_retry_prompt,
                generator_system=CV_TAILORING_REFINEMENT_PROMPT,
                reviewer_prompt_fn=coverage_reviewer_prompt_fn(
                    _build_cv_review_prompt, keyword_ledger
                ),
                reviewer_system=_CV_REVIEW_SYSTEM_PROMPT,
                provider=provider,
                max_retries=LLM_REVIEW_MAX_RETRIES,
                generator_max_tokens=CV_GENERATION_MAX_TOKENS,
                chain_id="cv_tailoring",
            )

            # US187: deterministically nest source projects under their parent
            # position (or the standalone list). The LLM tailors prose; code disposes.
            # MUST precede the language pass: these are verbatim profile copies, and
            # nesting after review shipped English project bullets in a German CV
            # (blind PQ 2026-07-04).
            tailored = _nest_projects(
                TailoredCVData.model_validate(tailored_raw), profile_json
            )

            # ADR-038 enforcement: ensure skill tags + prose (incl. project bullets)
            # are all in the target-job language (the directive alone leaks
            # discipline-skill phrases — #1). Carries the ledger: this pass is the
            # LAST writer, so the US213 coverage gate must also watch its rewording
            # (#122 follow-up).
            tailored_raw = await _review_cv_language(
                tailored.model_dump(mode="json"), resolve_jd_language(job), provider,
                keyword_ledger=keyword_ledger,
            )

            tailored = TailoredCVData.model_validate(tailored_raw)

            # PQ F7: deterministically copy the profile's certifications verbatim
            # (ADR-040 truthfulness) — never routed through the LLM. Covers both the
            # single-call and segmented paths, since both converge here.
            tailored = _apply_certifications(tailored, profile_json)

            # #118: enforce reverse-chronological work order (newest start first)
            # here — the one site where tailored_data + content_snapshot are
            # established — instead of trusting the LLM's echo of the input order.
            tailored = _enforce_work_order(tailored)

            # E042/US238 fix round: back-fill profile WorkEntry.ids onto the tailored
            # entries. The single-call path's schema omits `id` (and the language pass
            # can drop carried ids), but the condense loop's budget lookup is keyed by
            # them. MUST run after _enforce_work_order — the positional fallback for
            # ambiguous company+role pairs relies on both lists sharing the enforced
            # reverse-chronological order. Uses the SORTED profile_json (still bound
            # here; the photo step below rebinds the name to the raw profile dict).
            tailored = _backfill_work_ids(tailored, profile_json)

            # #234 (Tiramisu founder-acceptance F1/F2): deterministically restore any
            # verbatim vault bullet that carries a claimable Keyword Ledger concept the
            # writer's draft dropped entirely. MUST run after _backfill_work_ids — it is
            # keyed by the same profile WorkEntry.id the budget uses. Uses the SORTED
            # profile_json (still bound here; the photo step below rebinds the name to
            # the raw profile dict).
            tailored = _restore_ledger_bullets(tailored, profile_json, keyword_ledger, budget)

            # #172: collapse near-duplicate skill tags (the shared ats_audit
            # predicate) so the CV is clean even when the master profile still
            # carries twins. After the language pass, which rewords the tags.
            tailored = _dedup_skills(tailored)

            # #192: present a prioritised, JD-relevant SUBSET of the candidate's skills
            # instead of the whole master profile. Deterministic, downstream of the LLM +
            # language pass (so it ranks the final target-language tags): guarantees the
            # JD-required skills the candidate actually has, drops no-relevance tags over
            # the cap, and never invents a skill. Uses the SORTED profile_json (still bound
            # here — the photo step below rebinds `profile_json` to the raw profile dict).
            tailored = _tailor_skills_to_jd(
                tailored, profile_json, job_dict, keyword_ledger
            )

            # #250 (Tiramisu founder-acceptance blind-panel finding): drop bare skill
            # tags that are JD/ledger-concept echoes with no deterministic vault tie
            # (both blind reviewers independently flagged these as keyword-stuffing).
            # MUST run LAST in the skills pipeline -- after #192's cap/guarantee pass,
            # so nothing re-adds a dropped tag -- and BEFORE tailored_data/the ATS
            # audit are persisted below, so the coverage check reflects the final,
            # honest document.
            tailored = _drop_ungrounded_jd_echo_skills(
                tailored, profile_json, job_dict, keyword_ledger
            )

            # Populate photo_url from master profile's personal_info.
            # Stored path; resolved to base64 at render time in get_cv_html.
            profile_json = profile.profile_json or {}
            photo_url = (profile_json.get("personal_info") or {}).get("photo_url")
            if photo_url:
                tailored = tailored.model_copy(update={
                    "contact": tailored.contact.model_copy(update={"photo_url": photo_url})
                })

            from applire.services.cv_section_editor import build_content_snapshot
            record.content_snapshot = build_content_snapshot(tailored)

            record.tailored_data = tailored.model_dump()
            record.error_message = None
            record.error_code = None
            # ADR-039 + E037 PQ #2 (ATS "not available" race): persist the audit in the
            # SAME commit that flips status to 'ready', so "ready implies report available".
            # The frontend fetches the report once with no retry — if status went 'ready'
            # before the report was written, that single fetch read NULL and showed
            # "unavailable" permanently. status is set in memory FIRST so get_cv_html
            # (which is ready-guarded) sees it via autoflush; _update_ats_report issues
            # the one commit. An audit failure is non-fatal: it leaves ats_report NULL but
            # still commits status='ready'.
            record.status = CVGenerationStatus.ready.value
            # E042/US238 (ADR-051 §4): arm the bounded measure-and-condense loop with the
            # resolved target + feedforward budget already computed above. The loop counts
            # the rendered pages and deterministically condenses on overrun, rebuilds the
            # snapshot from the final data, then audits — all in the one ready-commit.
            condense_ctx = CondenseContext(budgets=budget, target=resolved_target_pages)
            await _update_ats_report(record, db, condense_ctx)   # ADR-039 — commits status + report together

        except Exception as exc:
            logger.exception("CV generation failed for %s: %s", cv_id, exc)
            try:
                _record_generation_failure(record, exc)
                await db.commit()
            except Exception:
                logger.exception("Failed to persist error status for CV %s", cv_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_cv(cv_id: uuid.UUID, db: AsyncSession) -> GeneratedCV:
    result = await db.execute(
        select(GeneratedCV).where(
            GeneratedCV.id == cv_id,
            GeneratedCV.deleted_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Generated CV {cv_id} not found")
    return record


async def _load_cv_ready(cv_id: uuid.UUID, db: AsyncSession) -> GeneratedCV:
    record = await _load_cv(cv_id, db)
    if record.status != CVGenerationStatus.ready.value:
        raise LookupError(
            f"CV {cv_id} is not ready (status: {record.status}). "
            "Poll GET /api/cv/{cv_id}/status until status='ready'."
        )
    return record


async def _html_to_pdf(html: str) -> bytes:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        await browser.close()
    return pdf_bytes


# ---------------------------------------------------------------------------
# ADR-039: ATS audit persistence helpers
# ---------------------------------------------------------------------------


async def _latest_keyword_ledger(db: AsyncSession, job_id: uuid.UUID) -> list[dict] | None:
    """Return the latest non-deleted GapAnalysis Keyword Ledger for *job_id* (ADR-048/US203).

    Mirrors the generation-path gap query. Used by the ATS audit to bucket missing
    keywords; ``None`` for legacy pre-E037 rows (then all missing default to honest-gap).
    """
    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap = result.scalar_one_or_none()
    return (gap.keyword_ledger or []) if gap else None


@dataclass
class CondenseContext:
    """Everything the post-render measure-and-condense loop needs (E042/US238,
    ADR-051 §4). Built ONLY by ``_render_cv_background`` — it has the resolved target
    and the feedforward budget in hand. Its presence is what arms condensation;
    ``_update_ats_report_by_id`` (the section-editor re-audit) passes ``None`` so that
    path stays audit-only (amendment §1)."""

    budgets: "BudgetResult"
    target: int


async def _resolve_audit_target(record: GeneratedCV, db: AsyncSession) -> int:
    """Resolve the target page count for the audit band when no CondenseContext is
    supplied (legacy NULL-``target_pages`` rows and the section-editor re-audit path):
    the row's persisted ``target_pages`` if present, else the user setting resolved the
    same way ``generate_cv`` does (ADR-051 §1)."""
    if record.target_pages is not None:
        return record.target_pages
    from applire.models.user_settings import UserSettings
    from applire.services.color_detection import _CE_STUB_USER_ID

    result = await db.execute(
        select(UserSettings.target_cv_pages).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    return resolve_target_pages(None, result.scalar_one_or_none())


async def _update_ats_report(
    record: GeneratedCV,
    db: AsyncSession,
    condense_ctx: CondenseContext | None = None,
) -> None:
    """ADR-039 + E042/US238: render → (bounded measure-and-condense) → audit → persist.

    With a ``condense_ctx`` (only the generation path supplies one) this runs the
    bounded loop: render, count pages, and if the document overruns ``target`` apply
    the deterministic ``condense_to_budget`` pass (max 2 iterations), re-rendering
    between passes and rebuilding ``content_snapshot`` from the final condensed data so
    the section editor never serves pre-condense bullets (amendment §2). Without a ctx
    — or when ``section_overrides`` already exist (a PATCH landed mid-generation,
    amendment §1) — it is audit-only, exactly today's behaviour. No LLM calls (§7).

    The page-length audit is target-aware and, when the loop exhausts its budget and
    the document still exceeds the region max, is told so for honest wording.

    Engine errors leave ats_report NULL, never raise — an audit failure must NEVER
    fail or alter generation status. Deliberately wipes any previous report on error:
    ADR-039 forbids a persisted report describing a state it was not computed from.
    """
    try:
        from applire.services.ats_audit import _audit_cv_text, extract_text_and_pages
        from applire.services.cv_budget import condense_to_budget
        from applire.services.cv_section_editor import (
            apply_overrides_to_tailored,
            build_content_snapshot,
        )

        # Bail rule (amendment §1): never condense over an override. A section PATCH can
        # land mid-generation; the audit render applies overrides the loop must not fight.
        do_condense = condense_ctx is not None and not record.section_overrides
        if condense_ctx is not None:
            target = condense_ctx.target
            region = condense_ctx.budgets.region
        else:
            target = await _resolve_audit_target(record, db)
            region = DEFAULT_REGION

        condensation_exhausted = False

        if do_condense:
            # Bounded measure-and-condense loop (max 2 condense iterations, ADR-051 §4/§6).
            text = ""
            count = 0
            for iteration in (1, 2):
                html = await get_cv_html(record.id, db)
                pdf = await _html_to_pdf(html)
                text, count = extract_text_and_pages(pdf)
                if count <= target:
                    break
                condensed, changed = condense_to_budget(
                    record.tailored_data, condense_ctx.budgets, iteration
                )
                if not changed:
                    # Nothing left to cut — the overrun is structural (education/skills).
                    condensation_exhausted = True
                    break
                record.tailored_data = condensed
                # Snapshot rebuild (amendment §2): rebuild IMMEDIATELY, in the same
                # breath as the tailored_data mutation — not after the loop settles.
                # Whole-branch review Finding 3: if the next iteration's re-render
                # raises (caught by the except below), the commit there must never
                # see condensed tailored_data paired with a stale pre-condense
                # snapshot (the section editor would re-serve pre-condense bullets,
                # the silent un-condense trap, reopened via this error path).
                record.content_snapshot = build_content_snapshot(
                    TailoredCVData.model_validate(record.tailored_data)
                )
            else:
                # Both iterations applied without meeting the target — measure the final
                # render and report the honest state.
                html = await get_cv_html(record.id, db)
                pdf = await _html_to_pdf(html)
                text, count = extract_text_and_pages(pdf)
                condensation_exhausted = count > target
        else:
            html = await get_cv_html(record.id, db)
            pdf = await _html_to_pdf(html)
            text, count = extract_text_and_pages(pdf)

        job = await db.get(JobAnalysis, record.job_analysis_id)
        tailored = TailoredCVData.model_validate(record.tailored_data)
        tailored = apply_overrides_to_tailored(
            tailored, record.content_snapshot, record.section_overrides
        )
        # ADR-048 / US203: the latest Keyword Ledger annotates each MISSING keyword as
        # missing-claimable vs missing-honest-gap (legacy rows have none → all honest-gap).
        ledger = await _latest_keyword_ledger(db, record.job_analysis_id)
        record.ats_report = _audit_cv_text(
            text,
            tailored,
            list(job.keywords or []) if job else [],
            ledger,
            page_count=count,
            target=target,
            region=region,
            condensation_exhausted=condensation_exhausted,
        ).model_dump()
    except Exception:
        logger.exception("ATS audit failed for CV %s — ats_report left NULL", record.id)
        record.ats_report = None
    # E043/US246 (ADR-052 §4): truthfulness self-audit of the FINAL data (post-
    # condense, overrides applied) rides the same single commit — "ready implies
    # report available" holds for the truthfulness panel exactly like the ATS one.
    # Deterministic-only and non-fatal by construction (build_self_audit_report
    # never raises); the section-editor re-audit path refreshes it too, so a
    # persisted report never describes content it was not computed from.
    try:
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.services.oracle.selfaudit import build_self_audit_report

        profile = await db.get(MasterProfile, record.profile_id)
        audited = apply_overrides_to_tailored(
            TailoredCVData.model_validate(record.tailored_data),
            record.content_snapshot,
            record.section_overrides,
        )
        record.truthfulness_report = await build_self_audit_report(
            profile.profile_json if profile else {},
            tailored_data=audited.model_dump(mode="json"),
        )
    except Exception:
        logger.exception(
            "Truthfulness self-audit failed for CV %s — report left NULL", record.id
        )
        record.truthfulness_report = None
    await db.commit()


async def _update_ats_report_by_id(cv_id: uuid.UUID) -> None:
    """BackgroundTasks entrypoint — opens its own session (the request session is gone by run time).

    The section-editor's post-edit re-audit path: passes NO CondenseContext, so it is
    strictly audit-only and never condenses (ADR-051 amendment §1)."""
    async with AsyncSessionLocal() as db:
        record = await db.get(GeneratedCV, cv_id)
        if record is not None:
            await _update_ats_report(record, db)


async def get_cv_ats_report(cv_id: uuid.UUID, db: AsyncSession) -> "ATSReportResponse":
    """Return the persisted ATS report for a CV (ADR-039).

    Raises LookupError if the CV is not found (→ 404 in the router).
    """
    from applire.schemas.ats import ATSReport, ATSReportResponse

    record = await _load_cv(cv_id, db)   # raises LookupError → 404 in the router
    # E037 PQ #2 hardening: a non-conforming stored report must degrade to report:null,
    # never raise (which would surface as an HTTP 500 the frontend can't recover from).
    report = None
    if record.ats_report:
        try:
            report = ATSReport.model_validate(record.ats_report)
        except Exception:
            logger.warning(
                "Stored ATS report for CV %s is malformed — returning report=null", record.id
            )
            report = None
    return ATSReportResponse(document_id=record.id, status=record.status, report=report)


async def get_cv_truthfulness_report(
    cv_id: uuid.UUID, db: AsyncSession
) -> "TruthfulnessReportResponse":
    """Return the persisted truthfulness report for a CV (ADR-052 / US246).

    Raises LookupError if the CV is not found (→ 404 in the router). A
    malformed stored report degrades to report=null, never a 500 (the E037
    PQ #2 hardening pattern).
    """
    from applire.schemas.oracle import TruthfulnessReport, TruthfulnessReportResponse

    record = await _load_cv(cv_id, db)
    report = None
    if record.truthfulness_report:
        try:
            report = TruthfulnessReport.model_validate(record.truthfulness_report)
        except Exception:
            logger.warning(
                "Stored truthfulness report for CV %s is malformed — returning report=null",
                record.id,
            )
            report = None
    return TruthfulnessReportResponse(
        document_id=record.id, status=record.status, report=report
    )


# ---------------------------------------------------------------------------
# Agent door: render_document (E044/US250, ADR-054)
# ---------------------------------------------------------------------------


async def render_agent_cv(
    content: dict,
    job_id: uuid.UUID,
    db: AsyncSession,
    template: CVTemplate = "classic_german",
    target_pages: int | None = None,
) -> GeneratedCV:
    """Render agent-authored CV content through Applire's templates (ADR-054).

    The caller is the author: content is persisted VERBATIM — no condense loop,
    no deterministic mutators, no language pass (ADR-054 §4). The only change
    Applire makes is the photo strip: ``contact.photo_url`` is never taken from
    the caller (``storage.read`` has no traversal guard — an arbitrary path
    would be read off disk and embedded into the PDF); when ``show_photo`` is
    true the profile's own ``personal_info.photo_url`` is backfilled instead.

    Page-target norms (ADR-051) are applied as ADVISORY only: the resolved
    target (per-call > user setting > region norm) feeds the page-length check
    in the ATS report; an overrun is reported, never fixed.

    Raises LookupError (unknown job / no profile) and ValueError /
    pydantic.ValidationError (content rejected — field paths included) for the
    MCP layer to map. Returns the committed, ready record with both reports
    persisted in the same commit ("ready implies reports available").
    """
    from applire.schemas.strict import find_unknown_fields

    job = await db.get(JobAnalysis, job_id)
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")

    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError("No profile found — import a CV first")

    unknown = find_unknown_fields(TailoredCVData, content)
    if unknown:
        raise ValueError(
            "Unknown fields for schema cv/1 (see resource schema://cv): "
            + ", ".join(sorted(unknown))
        )
    tailored = TailoredCVData.model_validate(content)

    # Photo strip (security): only the profile's own stored photo is trusted.
    profile_json = profile.profile_json or {}
    trusted_photo = (profile_json.get("personal_info") or {}).get("photo_url")
    tailored = tailored.model_copy(update={
        "contact": tailored.contact.model_copy(update={
            "photo_url": trusted_photo if tailored.show_photo else None
        })
    })

    from applire.models.user_settings import UserSettings
    from applire.services.color_detection import _CE_STUB_USER_ID
    from applire.services.cv_section_editor import build_content_snapshot

    settings_result = await db.execute(
        select(UserSettings.target_cv_pages).where(
            UserSettings.user_id == _CE_STUB_USER_ID
        )
    )
    user_setting = settings_result.scalar_one_or_none()

    record = GeneratedCV(
        job_analysis_id=job_id,
        profile_id=profile.id,
        tailored_data=tailored.model_dump(mode="json"),
        template=template,
        status=CVGenerationStatus.ready.value,
        origin="agent",
        target_pages=resolve_target_pages(target_pages, user_setting),
        content_snapshot=build_content_snapshot(tailored),
    )
    db.add(record)
    await db.flush()
    # Audit-only tail (no condense_ctx → never mutates content): renders,
    # measures, audits, self-audits, and commits status + both reports together.
    await _update_ats_report(record, db, None)
    await db.refresh(record)
    return record
