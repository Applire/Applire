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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from applire.services.cv_budget import BudgetResult
    from applire.storage.base import StorageProvider

from fastapi import BackgroundTasks
from applire.templates.filters import build_template_env
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
from applire.services.load_bearing import bullet_carries_figure
from applire.services.reviewer import review_and_refine
from applire.utils.budget_unit import budget_needs_unit
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


class UnknownWorkEntryIdError(ValueError):
    """E049 / ADR-067 clause 3 — the writer keyed prose to an id that is not in the
    vault's work-entry set. A hard, deterministic, FAIL-CLOSED error: an invented id
    would either be dropped silently (data loss) or matched fuzzily (the #303-class
    misassignment this design removes), so generation fails instead."""


def assemble_tailored_cv(prose: dict, profile_json: dict) -> dict:
    """Deterministically join the writer's PROSE onto the vault's FACTS, producing a
    TailoredCVData-shaped dict (E049 / ADR-067 clauses 2–3 — one assembly for both
    generation paths, ADR-066).

    ``prose`` is the LLM response shape: ``summary``, ``work`` (each entry an ``id``
    with ``bullets``/``projects``), ``skills`` — plus optional top-level ``projects``
    (the segmented path's standalone-projects writer). Everything factual — contact,
    employer, role, dates, education, languages — is carried verbatim from
    ``profile_json``, whose work_experience order (reverse-chronological, sorted by
    the caller) IS the document order: the model cannot reorder entries it never
    emits, which is what retired ``_enforce_work_order``.

    Contract (ADR-067 clause 3):
      * a prose id absent from the vault set → :class:`UnknownWorkEntryIdError`
        (fail closed — never fuzzy-matched, never silently dropped);
      * a vault entry the writer omitted keeps its factual line with empty bullets
        (logged — no silent entry loss, and no fabricated prose either).

    Certifications are NOT joined here — ``_apply_certifications`` remains their one
    writer (PQ F7). Pure: no LLM, no I/O.
    """
    work_src: list[dict] = [
        w for w in (profile_json.get("work_experience") or []) if isinstance(w, dict)
    ]
    vault_ids = [str(w.get("id") or "") for w in work_src]

    prose_by_id: dict[str, dict] = {}
    for entry in prose.get("work") or []:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "")
        if pid not in vault_ids or not pid:
            raise UnknownWorkEntryIdError(
                f"CV writer returned prose for unknown work-entry id {pid!r} "
                f"(vault ids: {vault_ids}) — failing closed (ADR-067 clause 3)"
            )
        prose_by_id[pid] = entry

    omitted = [i for i in vault_ids if i and i not in prose_by_id]
    if omitted:
        logger.warning(
            "CV writer omitted %d work-entry id(s) %s — their factual lines are "
            "kept with empty bullets (ADR-067 clause 3: no silent entry loss)",
            len(omitted), omitted,
        )

    work_history: list[dict] = []
    for w in work_src:
        wid = str(w.get("id") or "")
        p = prose_by_id.get(wid) or {}
        work_history.append({
            "id": wid,
            "company": w.get("company") or "",
            "role": w.get("role") or "",
            "start_date": w.get("start_date") or "",
            "end_date": w.get("end_date"),
            "bullets": [b for b in (p.get("bullets") or []) if isinstance(b, str)],
            "projects": [pr for pr in (p.get("projects") or []) if isinstance(pr, dict)],
        })

    return {
        # Identity/factual data sourced deterministically from the profile, never the
        # LLM (ADR-040/ADR-067). Photo is injected downstream as today.
        "contact": _contact_from_profile(profile_json),
        "summary": prose.get("summary") or "",
        "work_history": work_history,
        "skills": [s for s in (prose.get("skills") or []) if isinstance(s, str)],
        # Transcription, copied wholesale (ADR-067 clause 3 — no authored content,
        # no join key needed; the education section LLM call is retired).
        "education": [e for e in (profile_json.get("education") or []) if isinstance(e, dict)],
        "languages": _dedup_languages(
            [l for l in (profile_json.get("languages") or []) if isinstance(l, dict)]
        ),
        # Standalone projects from the segmented path's projects writer; the
        # single-call prose shape has none (vault standalone projects are nested
        # by _nest_projects downstream).
        "projects": [p for p in (prose.get("projects") or []) if isinstance(p, dict)],
    }


# E049 charter run 11: bilingual vault dirt — a profile built from a German CV
# plus an English-labelled source carries the SAME language twice ('Deutsch' +
# 'German'). The retired education-section LLM call used to launder this; the
# wholesale copy transcribes it, so assembly dedups deterministically. Mapping a
# language's German name to its English name is a finite lookup — a FACT under
# ADR-062 clause 1, not a judgement. First-seen row wins (vault order).
_LANGUAGE_NAME_CANON: dict[str, str] = {
    "deutsch": "german", "englisch": "english", "französisch": "french",
    "spanisch": "spanish", "italienisch": "italian", "polnisch": "polish",
    "türkisch": "turkish", "russisch": "russian", "niederländisch": "dutch",
    "portugiesisch": "portuguese", "arabisch": "arabic", "chinesisch": "chinese",
    "japanisch": "japanese", "koreanisch": "korean", "hindi": "hindi",
    "schwedisch": "swedish", "dänisch": "danish", "norwegisch": "norwegian",
    "finnisch": "finnish", "tschechisch": "czech", "ungarisch": "hungarian",
    "rumänisch": "romanian", "griechisch": "greek", "ukrainisch": "ukrainian",
}


def _dedup_languages(languages: list[dict]) -> list[dict]:
    """Collapse same-language rows that differ only in naming language
    ('Deutsch'/'German'). Keeps the first-seen row verbatim; never rewrites a
    name or level. Pure."""
    seen: set[str] = set()
    out: list[dict] = []
    for l in languages:
        name = (l.get("language") or "") if isinstance(l, dict) else ""
        key = name.strip().casefold()
        key = _LANGUAGE_NAME_CANON.get(key, key)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(l)
    return out


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
    *,
    output_language: str,
    provider: "LLMProvider",
    keyword_ledger: list[dict] | None = None,
    budget: "BudgetResult | None" = None,
    stated_limits_block: str | None = None,
    scope_positioning_block: str | None = None,
    vault_evidence_items: "list | None" = None,
) -> dict:
    """Outline-then-expand CV tailoring (ADR-047 §1 / US189) — the segmented path.

    One small outline call produces a shared tailoring directive; then one call per
    work-experience entry plus one each for summary / skills / standalone projects,
    every call capped at ``SEGMENT_MAX_TOKENS`` so no single output is large.

    E049 / ADR-067: returns the shared PROSE shape — ``summary``, ``work`` (each
    entry ``id`` + ``bullets``/``projects``), ``skills``, plus top-level
    ``projects`` — identical to the single-call writer's response. Facts are joined
    later by :func:`assemble_tailored_cv`, the ONE assembly both paths share
    (ADR-066). The education/languages section call is retired (clause 3:
    transcription, copied at assembly). Work order is not this function's concern —
    assembly follows the vault's sorted order.

    ``budget`` (E042/US237, ADR-051 §3) — the deterministic per-role bullet-count ceiling
    table, threaded into the outline call and each per-role work-section call so the model
    aims at the target page count directly. Not to be confused with the per-call TOKEN
    budget (``SEGMENT_MAX_TOKENS``) below — deliberately named ``token_budget`` to avoid
    the collision.

    ``stated_limits_block`` — the candidate's persisted denial statements rendered
    verbatim (:func:`applire.services.cross_document.render_stated_limits_block`), threaded
    into the summary and skills section calls so neither contradicts a stated limit.

    ``vault_evidence_items`` (#303) — the strongest-vault-evidence digest as
    ITEMS rather than a rendered block, because this path must scope them per
    work entry before rendering
    (:func:`applire.services.vault_evidence.filter_vault_evidence_for_owner`).
    The single-call writer sees every entry at once and can resolve ownership
    from each item's source path; a per-entry writer cannot, so handing it the
    whole digest would offer it another employer's achievement (ADR-071). An
    entry with no owned items gets no block, exactly as before.
    """
    from applire.prompts.cv_segmented import (
        OUTLINE_SYSTEM_PROMPT,
        PROJECTS_SECTION_SYSTEM_PROMPT,
        SKILLS_SECTION_SYSTEM_PROMPT,
        SUMMARY_SECTION_SYSTEM_PROMPT,
        WORK_SECTION_SYSTEM_PROMPT,
        build_outline_prompt,
        build_projects_prompt,
        build_skills_prompt,
        build_summary_prompt,
        build_work_section_prompt,
    )

    token_budget = SEGMENT_MAX_TOKENS

    # Reverse-chronological order for the per-entry calls (parity with the vault
    # order assembly will join on).
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

    from applire.services.vault_evidence import (
        filter_vault_evidence_for_owner,
        render_vault_evidence_block,
    )

    work_entries: list[dict] = []
    for w in work_src:
        owned_evidence = render_vault_evidence_block(
            filter_vault_evidence_for_owner(vault_evidence_items or [], w.get("id")),
            chain="cv",
        ) or None
        section = await provider.aparse_json(
            build_work_section_prompt(
                w, directive, job_analysis, keyword_gaps, output_language, keyword_ledger,
                budget, scope_positioning_block=scope_positioning_block,
                vault_evidence_block=owned_evidence,
            ),
            system=WORK_SECTION_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=token_budget,
        )
        work_entries.append({
            "id": w["id"],
            "bullets": list(section.get("bullets") or []),
            "projects": list(section.get("projects") or []),
        })

    summary_res = await provider.aparse_json(
        build_summary_prompt(
            directive, job_analysis, profile, output_language, keyword_ledger,
            stated_limits_block,
        ),
        system=SUMMARY_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )
    skills_res = await provider.aparse_json(
        build_skills_prompt(
            directive, job_analysis, profile, keyword_gaps, output_language, keyword_ledger,
            stated_limits_block,
        ),
        system=SKILLS_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )
    projects_res = await provider.aparse_json(
        build_projects_prompt(directive, job_analysis, profile, output_language),
        system=PROJECTS_SECTION_SYSTEM_PROMPT, temperature=0.3, max_tokens=token_budget,
    )

    return {
        "summary": summary_res.get("summary") or "",
        "work": work_entries,
        "skills": list(skills_res.get("skills") or []),
        "projects": list(projects_res.get("projects") or []),
    }


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
    *,
    output_language: str,
    provider: "LLMProvider",
    keyword_ledger: list[dict] | None = None,
    budget: "BudgetResult | None" = None,
    stated_limits_block: str | None = None,
    scope_positioning_block: str | None = None,
    vault_evidence_block: str | None = None,
    vault_evidence_items: "list | None" = None,
) -> dict:
    """Produce the tailored CV PROSE draft: single call on the fast path, segmented as
    the fallback (ADR-047 §1/§2). On a known-small declared cap, segment upfront;
    otherwise try the single large call and switch to segmented on truncation/timeout
    rather than doubling the budget into a timeout (the US188 'switch to segmented'
    recovery).

    E049 / ADR-067: both paths return the same PROSE shape — ``summary``, ``work``
    (id-keyed bullets/projects), ``skills`` — never facts. The caller runs the
    review + language chains on this shape and only then joins the vault facts via
    :func:`assemble_tailored_cv`.

    ``keyword_ledger`` (ADR-048 / US200) is surfaced into the prompt(s) as the
    claimable-vs-forbidden keyword split. ``budget`` (E042/US237, ADR-051 §3) is the
    deterministic per-role bullet-count ceiling table, threaded into whichever path runs.
    ``stated_limits_block`` is the candidate's persisted denial statements rendered
    verbatim, threaded into whichever path runs."""
    if await _should_segment_upfront():
        return await generate_cv_segmented(
            job_analysis, profile, keyword_gaps,
            output_language=output_language, provider=provider,
            keyword_ledger=keyword_ledger, budget=budget,
            stated_limits_block=stated_limits_block,
            scope_positioning_block=scope_positioning_block,
            vault_evidence_items=vault_evidence_items,
        )
    try:
        return await provider.aparse_json(
            build_user_prompt(
                job_analysis, profile, keyword_gaps,
                output_language=output_language,
                keyword_ledger=keyword_ledger,
                budget=budget,
                stated_limits_block=stated_limits_block,
                scope_positioning_block=scope_positioning_block,
                vault_evidence_block=vault_evidence_block,
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
            job_analysis, profile, keyword_gaps,
            output_language=output_language, provider=provider,
            keyword_ledger=keyword_ledger, budget=budget,
            stated_limits_block=stated_limits_block,
            scope_positioning_block=scope_positioning_block,
            vault_evidence_items=vault_evidence_items,
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
    #
    # ADR-072 clause 5: the company-name index is UNAMBIGUOUS ONLY, and a name
    # matching two or more tenures resolves to nothing. `setdefault` silently
    # picked whichever tenure came first in vault order, so a candidate promoted
    # inside one employer had the project nested under the WRONG role — evidence
    # from the senior tenure rendered under the junior one (reproduced
    # 2026-08-02). Falling through to the standalone project list is honest: it
    # never asserts an ownership the data does not support.
    work_by_id: dict[str, dict] = {}
    company_hits: dict[str, list[dict]] = {}
    for w in profile_json.get("work_experience") or []:
        wid = w.get("id")
        if wid:
            work_by_id[str(wid)] = w
        company = (w.get("company") or "").strip().lower()
        if company:
            company_hits.setdefault(company, []).append(w)
    work_by_company: dict[str, dict] = {
        c: ws[0] for c, ws in company_hits.items() if len(ws) == 1
    }

    data = tailored.model_dump()
    work_history = data.get("work_history") or []

    def _match_tailored_index(parent_work: dict) -> int | None:
        """Locate the TAILORED entry that renders ``parent_work``.

        By ``id`` — the identity ``assemble_tailored_cv`` establishes
        structurally on every tailored entry (E049/ADR-067), and the reason
        ``TailoredWorkEntry.id`` exists at all. Every sibling pass in this
        module (``_apply_role_facts``, ``_restore_ledger_bullets``) already
        matches this way; this one did not, and that was the residual half of
        ADR-072 clause 5: resolving the SOURCE entry by id and then throwing
        the id away to re-match the tailored side on company+role strings.
        With a rehire into the same title, "first match wins" put the current
        tenure's project under the tenure that ended years earlier (found by
        this branch's own adversarial pass, 2026-08-02).

        The string path survives only for legacy/id-less tailored data — the
        case ``TailoredWorkEntry.id``'s own docstring provides for ("Empty for
        legacy records / mock fixtures — nesting then matches on company+role
        instead") — and both of its branches are ambiguity-guarded: an
        association that cannot identify exactly one entry falls through to the
        standalone project list, which never asserts an ownership the data does
        not support.
        """
        wid = str(parent_work.get("id") or "")
        if wid and any(str(w.get("id") or "") for w in work_history):
            for idx, w in enumerate(work_history):
                if str(w.get("id") or "") == wid:
                    return idx
            # An id that names no tailored entry is not a licence to guess.
            return None

        company_l = (parent_work.get("company") or "").strip().lower()
        role_l = (parent_work.get("role") or "").strip().lower()
        if company_l and role_l:
            hits = [
                idx for idx, w in enumerate(work_history)
                if (w.get("company") or "").strip().lower() == company_l
                and (w.get("role") or "").strip().lower() == role_l
            ]
            if len(hits) == 1:
                return hits[0]
            if hits:
                return None  # two tenures, same title — indistinguishable
        if not company_l:
            return None
        hits = [
            idx
            for idx, w in enumerate(work_history)
            if (w.get("company") or "").strip().lower() == company_l
        ]
        return hits[0] if len(hits) == 1 else None

    from applire.services.ats_audit import _norm as _ats_norm
    from applire.services.bullet_cuts import log_deletion

    standalone: list[dict] = []
    for proj in source_projects:
        name = (proj.get("name") or "").strip()
        if not name:
            continue
        entry = TailoredProjectEntry(name=name, bullets=_project_bullets(proj)).model_dump()
        if not project_has_content(entry):
            # #312: a vault project with no description, responsibilities or
            # achievements has nothing to put under its heading, and every
            # template renders the heading unconditionally. Nesting it would
            # only manufacture the orphan bold line for the render guard to
            # remove again.
            log_deletion(
                "_nest_projects", "source project carries no bullet text", name,
            )
            continue

        parent_ref = proj.get("associated_experience")
        target_idx: int | None = None
        if parent_ref is not None:
            parent_key = str(parent_ref).strip()
            parent_work = work_by_id.get(parent_key) or work_by_company.get(
                parent_key.lower()
            )
            if parent_work is not None:
                target_idx = _match_tailored_index(parent_work)

        if target_idx is not None:
            # E049 charter run 11: the writer's response schema now carries nested
            # projects, so the writer may already have tailored THIS project onto
            # the entry — appending the vault copy next to it rendered the same
            # project heading twice with overlapping bullets. Same-name (fact,
            # normalised equality) ⇒ the reviewed, tailored version already on the
            # page wins; the verbatim copy is not appended.
            existing_names = {
                _ats_norm(p.get("name") or "")
                for p in work_history[target_idx].get("projects") or []
            }
            if _ats_norm(name) in existing_names:
                continue
            work_history[target_idx].setdefault("projects", []).append(entry)
        else:
            already = [
                _ats_norm(p.get("name") or "")
                for p in list(data.get("projects") or []) + standalone
            ]
            if _ats_norm(name) in already:
                continue
            standalone.append(entry)

    _suppress_duplicate_project_bullets(work_history)

    data["work_history"] = work_history
    # #312: the writer's own standalone projects (build_projects_prompt) can also
    # arrive with an empty `bullets` list — the response schema permits it. Same
    # rule, same predicate: no bullets, no heading.
    data["projects"] = [
        p
        for p in (data.get("projects") or []) + standalone
        if project_has_content(p)
    ]
    return TailoredCVData.model_validate(data)


def _suppress_duplicate_project_bullets(work_history: list[dict]) -> None:
    """#169: the LLM often emits the same sentence twice — once as a role bullet and
    once inside the project nested under that role (the segmented per-entry writer
    emits ``bullets`` and ``projects`` in one JSON, so overlap is structural). Drop
    each nested-project bullet whose normalized form equals any of the PARENT role's
    own bullets. Deterministic; reuses ``ats_audit._norm`` (NFKC + dash→space +
    casefold) so "Code-Review" ≡ "code review".

    **#312 (2026-08-07) — a project this pass empties is now DROPPED.** The
    original rule kept it ("US187: the heading still carries the project"), and
    charter run #7 showed what that produces: ``SAP-Rollout bei Rasselstein``
    as a bold heading over nothing, one line under the role bullet that had
    just absorbed its only sentence. A heading carries nothing; the fact is not
    lost, it is on the parent role. ``cv_budget.condense_to_budget`` already
    drops a project whose last bullet it cuts — this is the same rule at the
    other pass that can empty one. Both log the deletion (never silent).

    Mutates ``work_history`` in place; standalone projects are never deduped
    against a role, but a standalone project that arrives content-free is
    dropped by the same #312 rule in the caller / render guard.
    """
    from applire.services.ats_audit import _norm
    from applire.services.bullet_cuts import log_deletion

    for w in work_history:
        role_norms = {
            _norm(b) for b in (w.get("bullets") or []) if isinstance(b, str) and b.strip()
        }
        surviving: list[dict] = []
        for proj in w.get("projects") or []:
            if not isinstance(proj, dict):
                surviving.append(proj)
                continue
            if role_norms:
                proj["bullets"] = [
                    b
                    for b in (proj.get("bullets") or [])
                    if not (isinstance(b, str) and _norm(b) in role_norms)
                ]
            if not project_has_content(proj):
                log_deletion(
                    "_suppress_duplicate_project_bullets",
                    "project left with no bullet text",
                    proj.get("name") or proj,
                    role_id=str(w.get("id") or ""),
                )
                continue
            surviving.append(proj)
        if w.get("projects") is not None:
            w["projects"] = surviving


def _apply_certifications(tailored: TailoredCVData, profile_json: dict) -> TailoredCVData:
    """Deterministically copy the Master Profile's certifications verbatim into
    ``tailored.certifications`` (PQ F7 / ADR-040 truthfulness).

    Certifications are FACTUAL data, like contact info — never routed through an
    LLM JSON schema. This is a pure passthrough (no selection, no LLM, no I/O),
    called after the LLM tailoring step(s) in both the single-call and segmented
    generation paths, mirroring ``_nest_projects``. Returns a new TailoredCVData;
    the input is left unmutated.

    ADR-061 clause 3: an ``unconfirmed`` certification is excluded — it cannot
    back a CV line. Never fabricated as a drop either; the candidate's own
    profile-confirmation action is what promotes it, not a CV render.
    """
    source_certs = [
        c for c in (profile_json.get("certifications") or [])
        if not (isinstance(c, dict) and c.get("status") == "unconfirmed")
    ]
    if not source_certs:
        return tailored
    return tailored.model_copy(
        update={"certifications": [TailoredCertification.model_validate(c) for c in source_certs]}
    )


# E049 / ADR-067 clause 3: `_enforce_work_order` (#118) and `_backfill_work_ids`
# (E042/US238) are DELETED, not relocated. The writer no longer emits entries at
# all — assemble_tailored_cv joins prose onto the vault's sorted work list, so
# document order is the vault order structurally, and every tailored entry carries
# its vault id by construction. Both passes' reasons to exist disappeared with the
# fields they repaired.


def _apply_role_facts(tailored: TailoredCVData, profile_json: dict) -> TailoredCVData:
    """#328 (ADR-062 clause 1) — deterministically copy each work entry's
    quantified role facts (``team_size`` / ``budget_managed`` /
    ``industry_context``) from the vault ``WorkEntry`` onto the matching
    tailored entry, so they can be rendered as document furniture (a per-role
    sub-header line) independent of the writer LLM's prose.

    These three fields were captured by the CV-import extractor, carried
    through reconciliation, asked for by the interview and verified by the
    response reviewer, and counted toward profile completeness — but had ZERO
    readers on the generation side.

    **This pass is the ONLY writer, and that is enforced structurally rather
    than by instruction.** The three fields are written from the vault on every
    tailored entry *unconditionally* — including as ``None`` when the vault is
    silent, when no vault entry matches the tailored entry's id, and when the
    profile carries no work history at all. Do not add an early return that
    skips the write: that leaves whatever the draft happened to carry in place.

    The reason this must fail safe rather than fail open: these values render as
    document **furniture** (a labelled per-role sub-header), which presents them
    to a recruiter as authoritative structured data rather than as prose a
    reader discounts as authored — so a wrong value here costs more than a wrong
    sentence. It is not sufficient that the writer prompt's schema omits these
    fields: ``TailoredWorkEntry`` carries them, Pydantic's default ``extra``
    policy accepts them, and #229 is this repository's own precedent for a
    prompt schema acting as a dead control. An instruction is not a guarantee.

    Matched by the SAME work-entry ``id`` identity ``assemble_tailored_cv``
    establishes structurally (E049/ADR-067 — never matched by company-name
    string, which risks a wrong role's figures on a re-hire /
    same-employer-twice profile). Mirrors ``_apply_certifications``: pure
    passthrough, no LLM, no I/O. Returns a new TailoredCVData; the input is
    left unmutated.
    """
    vault_by_id: dict[str, dict] = {
        str(w.get("id") or ""): w
        for w in (profile_json.get("work_experience") or [])
        if isinstance(w, dict) and w.get("id")
    }
    if not tailored.work_history:
        return tailored

    changed = False
    new_work: list[TailoredWorkEntry] = []
    for w in tailored.work_history:
        # No match is NOT a reason to skip the write — an unmatched or re-keyed
        # entry would otherwise be a laundering path for a drafted value.
        vault_entry = (vault_by_id.get(w.id) if w.id else None) or {}

        team_size = vault_entry.get("team_size")
        # ``isinstance`` rather than truthiness: 0 is a real answer ("no direct
        # reports"), and the vault is authoritative without being trusted to be
        # well-typed. ``bool`` is an int subclass and is not a headcount.
        team_size = team_size if isinstance(team_size, int) and not isinstance(team_size, bool) else None
        # #382 (PO decision 2026-08-08, Option A): a budget wording that states
        # no unit does not reach the document at all. "6000000" is six million of
        # *something*, and this line is furniture — read as authoritative
        # structured data rather than as prose a reader discounts — so the only
        # honest renderings are "ask" and "say nothing"; a guessed currency would
        # be fabrication. The VAULT keeps the value (real testimony; the ADR-069
        # scope floor still counts it) and it re-enters this line the moment a
        # unit is confirmed. The omission is never silent: the Health hub raises
        # it and the master profile page offers the fix at the field.
        budget_managed = vault_entry.get("budget_managed") or None
        if budget_needs_unit(budget_managed):
            budget_managed = None
        industry_context = vault_entry.get("industry_context") or None

        if (w.team_size, w.budget_managed, w.industry_context) == (
            team_size, budget_managed, industry_context
        ):
            new_work.append(w)  # already correct — nothing to write
            continue

        changed = True
        new_work.append(w.model_copy(update={
            "team_size": team_size,
            "budget_managed": budget_managed,
            "industry_context": industry_context,
        }))

    if not changed:
        return tailored
    return tailored.model_copy(update={"work_history": new_work})


def _cap_bullets(
    bullets: list[str],
    max_bullets: int,
    *,
    concept_groups: Sequence[Sequence[str]] = (),
    external_text: str = "",
    context: dict | None = None,
) -> list[str]:
    """Trim ``bullets`` down to ``max_bullets``, sharing ONE ranking
    implementation with ``cv_budget.condense_to_budget`` — both delegate to
    :func:`applire.services.bullet_cuts.rank_cuts` (ADR-066: one logical
    operation, one implementation; these two drifted apart before #377).

    Cut order: a bullet that is the sole carrier of a claimable Keyword-Ledger
    concept goes LAST (ADR-072 clause 1); then bullets carrying NO quantified
    figure (:func:`applire.services.load_bearing.bullet_carries_figure`, #377 /
    ADR-067 clause 4); then, within an equal status, the later-listed bullet
    first, so the earliest (typically strongest) bullets survive.

    This is the pass that lost #423. The Weberit role's logged ceiling was
    ``max 5 (tier: top)`` against 6 settled bullets, and *"Verantwortung für
    den Sauberraumbereich (Kunststoff- und Kosmetik-Verpackungen) seit 2021"*
    — the candidate's only packaging evidence, against a packaging
    manufacturer's JD — ranked last on both of the then-existing criteria: a
    bare year is not a quantified figure, and it was listed last. No prompt
    change could reach it, because the content was already approved by every
    reviewer when this pass deleted it.

    ``concept_groups`` is ``BudgetResult.claimable_concepts`` (one group per
    claimable ledger entry) and ``external_text`` is the rest of the document —
    a concept carried there is covered whatever happens here. Both default to
    empty, in which case the ranking is exactly #377's and this function
    behaves as it did before ADR-072.

    #377: deterministic code may cap and order, but it may not choose which
    evidence is STRONGEST by keyword-ledger proxy -- whether a bullet carries
    a figure is a FACT (ADR-062 clause 1), computed via the shared extractor,
    not a guess from whether the bullet happens to repeat a ledger surface
    form. The prior ``is_hit`` (ledger-keyword) ranking is retired: it cut a
    real, quantified safety-ratio bullet ("Unfallquote (LTIF) von 8,2 auf 3,1
    gesenkt") ahead of keyword-bearing filler that carried no number at all.
    The model's own bullet ORDER is preserved as its own relevance judgement
    within each figure-status tier -- this pass only caps, it never reorders
    beyond what the tie-break requires.

    Unlike the restore-path reordering in ``_restore_ledger_bullets`` (which
    intentionally regroups hits-first), this preserves the SURVIVORS' original
    relative order -- entries this pass didn't otherwise touch must not have
    their bullet order perturbed, only cut down to size.

    No-op (returns ``bullets`` unchanged, same object) when already within budget.
    """
    if len(bullets) <= max_bullets:
        return bullets
    from applire.services.bullet_cuts import apply_cuts, log_cuts, rank_cuts

    # Ascending (carries_figure, -order) below the coverage criterion rank_cuts
    # prepends: figure-less (False) sorts before figure-bearing (True); within a
    # tie, the higher (later) order sorts first -- i.e. later-listed first.
    cuts = rank_cuts(
        bullets,
        [(bullet_carries_figure(b), -i) for i, b in enumerate(bullets)],
        keep=max_bullets,
        concept_groups=concept_groups,
        external_text=external_text,
    )
    log_cuts("_cap_bullets", cuts, ceiling=max_bullets, **(context or {}))
    return apply_cuts(bullets, cuts)


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
    budget with no restoration ever happening. Figure-less bullets are cut before
    figure-bearing bullets (#377 / ADR-067 clause 4 — see ``_cap_bullets``),
    later-listed before earlier — mirrors ``cv_budget.condense_to_budget``'s cut
    order exactly. Entries already within budget keep their original bullets
    AND order untouched.
    """
    if not keyword_ledger:
        return tailored

    from applire.services.ats_audit import _norm, surface_present
    from applire.services.keyword_ledger import (
        is_load_bearing,
        verified_missing_claimable,
        verified_missing_load_bearing,
    )
    from applire.services.load_bearing import stringify_draft

    # NOTE: deliberately no early return when ``missing`` is empty — an entry can
    # still be over its RoleBudget ceiling with nothing left to restore (the #122
    # coverage-review loop pushing an ADD with no ceiling awareness of its own),
    # and the per-entry loop below must run to enforce that ceiling regardless.
    draft_json = tailored.model_dump(mode="json")
    missing = verified_missing_claimable(draft_json, keyword_ledger)
    # #315: a LOAD-BEARING concept (a `direct`+`claimable` figure a hiring
    # reviewer checks for by name) is missing its evidence even when a bare
    # keyword mention elsewhere (skills list, summary) already satisfies the
    # whole-document check above -- that check alone let charter run #7 ship
    # "Budgetverantwortung" as a tag while its "6 Mio. €" bullet was silently
    # dropped by the writer and never restored. Union in, deduped by concept
    # (verified_missing_claimable already covers a concept absent everywhere;
    # this only adds concepts present ONLY as a bare tag).
    already = {e.get("concept") for e in missing}
    for entry in verified_missing_load_bearing(draft_json, keyword_ledger):
        if entry.get("concept") not in already:
            missing.append(entry)
            already.add(entry.get("concept"))

    # Vault entries keyed by id — the identity ``assemble_tailored_cv`` establishes
    # structurally on every tailored entry (E049/ADR-067).
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

    # ADR-072 clause 1 needs a whole-document coverage picture, so the cap can
    # tell "this bullet is the only place the concept appears" from "it is
    # repeated in the skills list". Everything outside work_history is fixed
    # (no pass below cuts it); the work entries are rebuilt per iteration from
    # the entries already processed plus the ones still untouched, so a cut
    # made in an earlier entry is correctly absent from the picture.
    concept_groups = budget.claimable_concepts if budget is not None else ()
    non_work = {k: v for k, v in draft_json.items() if k != "work_history"}
    pending_dumps = [w.model_dump(mode="json") for w in tailored.work_history]

    def _external_text(index: int, entry_dict: dict) -> str:
        """Everything the cap at ``index`` cannot itself remove.

        The entry's OWN non-bullet text counts as external — a concept named in
        the position title or the entry summary is covered whatever this cap
        decides. Both ceiling enforcers below use this, so they cannot disagree
        about what "covered elsewhere" means.
        """
        if not concept_groups:
            return ""
        others = new_work + [{**entry_dict, "bullets": []}] + pending_dumps[index + 1:]
        return stringify_draft({**non_work, "work_history": others})

    for w_index, w in enumerate(tailored.work_history):
        w_dict = w.model_dump(mode="json")
        eid = str(w_dict.get("id") or "")
        existing_bullets = [b for b in (w_dict.get("bullets") or []) if isinstance(b, str)]
        existing_norms = {_norm(b) for b in existing_bullets}

        vault_entry = vault_by_id.get(eid)
        # #315 follow-up: a restored bullet answering a LOAD-BEARING concept is
        # tracked separately from every other restoration, so it can be placed
        # ahead of generic pre-existing hits before the ceiling cap below --
        # see that cap's comment for why this split exists.
        restored_load_bearing: list[str] = []
        restored_other: list[str] = []
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
                matched_entry = remaining[hit_idx]
                if is_load_bearing(matched_entry):
                    restored_load_bearing.append(vb)
                else:
                    restored_other.append(vb)
                existing_norms.add(vb_norm)
                remaining.pop(hit_idx)

        rb = budget.roles.get(eid) if budget is not None else None
        restored = restored_load_bearing + restored_other

        if restored:
            changed = True
            # #315 follow-up (coordinator finding): the naive `existing_hits +
            # restored` order let the ceiling cap below silently cancel a
            # restoration whenever a role's PRE-EXISTING hit bullets already
            # filled max_bullets -- exactly the real charter run #7 shape
            # (Weberit shipped at its 5-bullet ceiling). A restored
            # LOAD-BEARING bullet is placed ahead of every generic
            # pre-existing hit, because a generic hit is not entitled to bump
            # a figure a hiring reviewer checks for by name. A restored
            # NON-load-bearing bullet keeps the ORIGINAL ordering (after
            # existing hits), so #234's established behaviour for the common
            # case is unchanged -- only load-bearing restorations get the
            # stronger placement.
            existing_hits = [b for b in existing_bullets if _is_hit(b)]
            no_hits = [b for b in existing_bullets if not _is_hit(b)]
            hits = restored_load_bearing + existing_hits + restored_other
            ordered = hits + no_hits
            if rb is not None and len(ordered) > rb.max_bullets:
                # THE shared ranking (ADR-072 clause 1 / ADR-066), not a
                # positional truncation. An earlier revision cut `ordered` by
                # position on the argument that the order above is already
                # coverage-aware, because ``no_hits`` sit last and yield first.
                # That holds only until ``no_hits`` runs out: past that point
                # the cut eats ``existing_hits`` positionally, and `_is_hit` --
                # "does this bullet contain ANY claimable surface form" -- can
                # neither tell a concept's sole carrier from a bullet repeating
                # a term the skills list already carries, nor emit clause 4's
                # WARNING, because it never computes sole-carrier status at
                # all. Two enforcers of one rule, one of them unaware of it
                # (found by this branch's own adversarial pass, 2026-08-02).
                #
                # The #315 priority is preserved by CARRYING it in the tier key
                # rather than in the list order: a restored load-bearing bullet
                # outranks a pre-existing hit, which outranks a no-hit. Coverage
                # sits above all three, which is not a conflict in practice --
                # a load-bearing restoration is restored precisely because its
                # concept was verifiably absent, so it is a sole carrier too.
                from applire.services.bullet_cuts import apply_cuts, log_cuts, rank_cuts

                lb = set(restored_load_bearing)
                cuts = rank_cuts(
                    ordered,
                    # Ascending = cut FIRST, so each flag is written so that the
                    # thing worth KEEPING is True: a load-bearing restoration
                    # sorts last of all, a claimable hit after a no-hit, and
                    # within a tier the later-listed bullet yields first.
                    [(b in lb, _is_hit(b), -i) for i, b in enumerate(ordered)],
                    keep=rb.max_bullets,
                    concept_groups=concept_groups,
                    external_text=_external_text(w_index, w_dict),
                )
                log_cuts(
                    "_restore_ledger_bullets", cuts,
                    work_entry_id=eid, ceiling=rb.max_bullets,
                )
                ordered = apply_cuts(ordered, cuts)
            # ADR-061 clause 8 ("every drop is diagnosable from the log
            # alone"): even front-ordered, a load-bearing restoration can
            # still be cancelled if there are MORE load-bearing restorations
            # than the ceiling has room for. That must never be silent --
            # this is precisely the "guard runs, reports success internally,
            # document unchanged" failure mode #315 was filed over.
            surviving = set(ordered)
            dropped_load_bearing = [b for b in restored_load_bearing if b not in surviving]
            if dropped_load_bearing:
                logger.warning(
                    "LOAD_BEARING_RESTORE_DROPPED (#315): work entry id=%s ceiling "
                    "max_bullets=%s could not fit %d load-bearing restored bullet(s) "
                    "even after ordering them ahead of %d pre-existing hit bullet(s) "
                    "-- dropped: %r",
                    eid, rb.max_bullets if rb is not None else None,
                    len(dropped_load_bearing), len(existing_hits), dropped_load_bearing,
                )
            w_dict["bullets"] = ordered
            new_work.append(w_dict)
            continue

        # Nothing to restore into this entry, but #234-adjacent friction finding:
        # upstream passes (the writer, and the #122 coverage-review loop) have no
        # ceiling awareness of their own -- a review-driven ADD can leave an entry
        # over its RoleBudget with nothing downstream to trim it back except a
        # page-overrun condense pass that may never fire. Enforce the ceiling
        # deterministically here too, mirroring cv_budget.condense_to_budget's cut
        # order (figure-less bullets first, later-listed first within a tie -- a
        # figure-bearing bullet is only ever cut once every figure-less bullet is
        # gone; #377 / ADR-067 clause 4). Untouched
        # (under-ceiling) entries keep their original bullets AND order exactly.
        if rb is not None and len(existing_bullets) > rb.max_bullets:
            capped = _cap_bullets(
                existing_bullets, rb.max_bullets,
                concept_groups=concept_groups,
                external_text=_external_text(w_index, w_dict),
                context={"work_entry_id": eid},
            )
            if capped != existing_bullets:
                changed = True
                w_dict["bullets"] = capped

        new_work.append(w_dict)

    if not changed:
        return tailored
    return tailored.model_copy(
        update={"work_history": [TailoredWorkEntry.model_validate(w) for w in new_work]}
    )


def _prefer_measured_outcomes(
    tailored: TailoredCVData, profile_json: dict, lang: str = "de"
) -> TailoredCVData:
    """#261 — prefer MEASURED OUTCOMES over TARGETS for the same initiative.

    Ground truth (run-4 blind hiring-panel finding, 2026-07-24): the tailored
    CV kept a naked "targeting a 70% reduction" bullet sitting next to a
    properly quantified measured win for the SAME initiative — read by the
    blind hiring manager as "intentionally blurring aspiration and outcome",
    one of two named invite-"no" reasons.

    Deterministic post-draft guard, mirroring ``_restore_ledger_bullets``'s
    own idiom (runs after it, on the SAME vault WorkEntry.id identity
    ``assemble_tailored_cv`` establishes structurally): for every work entry whose bullets
    contain a target-phrase bullet with a safely-paired measured outcome
    elsewhere in the vault (owner-scoped, #196/#244 attribution machinery —
    see ``services.outcome_preference`` module docstring for the full pairing
    rule and its fabrication guard), the outcome is surfaced and the naked
    target is either dropped (already present as its own bullet) or reframed
    with the target folded in as explicit context. MUST run after
    ``_restore_ledger_bullets`` so a restored vault bullet is also subject to
    this preference. Pure; ``tailored`` is left unmutated. No-op (returns the
    SAME object) when no work entry changes.

    ``lang`` (ADR-038) — the framing word ("measured"/"gemessen") follows the
    document's own OUTPUT language, never the UI language: this text is
    written into generated document content, same invariant as
    ``templates.labels.cv_labels``.
    """
    from applire.services.oracle.matchers import build_vault_index
    from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

    index = build_vault_index(profile_json)

    changed = False
    new_work: list[TailoredWorkEntry] = []
    for w in tailored.work_history:
        owner_id = (w.id or "").strip()
        new_bullets = prefer_measured_outcomes_for_owner(
            list(w.bullets), owner_id, index.units, lang
        )
        if new_bullets != w.bullets:
            changed = True
            new_work.append(w.model_copy(update={"bullets": new_bullets}))
        else:
            new_work.append(w)

    if not changed:
        return tailored
    return tailored.model_copy(update={"work_history": new_work})


def _is_more_specific(candidate: str, kept: str) -> bool:
    """ADR-072 clause 3: which of two page-duplicate tags survives.

    Token containment is the primary signal ('MES (Maschinendaten…)' strictly
    contains 'MES'). It cannot decide the German-compound shape, because the
    compound and its head are single DISJOINT tokens — 'dreischichtbetrieb' is
    not a superset of 'schichtbetrieb' — so before this the survivor there was
    whichever the writer happened to emit FIRST. A deterministic pass whose
    output depends on emission order is a defect by itself (it is SF-WRITE.19's
    instability in a second place), so the longer compound wins explicitly.
    """
    from applire.services.ats_audit import skill_tokens

    tc, tk = skill_tokens(candidate), skill_tokens(kept)
    if tc > tk:
        return True
    if len(tc) == 1 and len(tk) == 1:
        (c,), (k,) = tc, tk
        return len(c) > len(k) and c.endswith(k)
    return False


def _dedup_skills(tailored: TailoredCVData) -> TailoredCVData:
    """#172: collapse duplicate skill tags so the rendered CV stays clean even
    when the master profile is still dirty (the reconciler merges going forward, but
    existing profiles carry twins like 'Team Leadership' + 'Team Leadership and
    Mentorship').

    #386 (E049 clause-6 disposition): the predicate is now the PAGE-scope
    :func:`ats_audit.skills_page_dupe` — on the rendered list, 'MES' next to
    'MES (Maschinendaten- und Betriebsdatenerfassung)' is a visible duplicate
    with no second meaning, even though the vault-merge predicate correctly
    refuses to auto-merge that pair in the reconciler.

    Keeps the first-seen occurrence's POSITION (stable order) but upgrades its name
    to the more-specific variant when a later dupe strictly contains it. Pure;
    input unmutated. Must run AFTER the ADR-038 language pass, which rewords tags.

    ADR-072 clause 4: every drop is logged with the surviving tag it was judged
    a duplicate of. This is the pass that silently deleted #423's
    ``Verpackungsindustrie`` as a "page duplicate" of the unrelated
    ``Industrie 4.0`` — a loss that took four captured runs to attribute
    precisely because it left nothing behind.
    """
    from applire.services.ats_audit import skills_page_dupe
    from applire.services.bullet_cuts import log_deletion

    original = list(tailored.skills or [])
    kept: list[str] = []
    for s in original:
        dup_idx = next(
            (i for i, k in enumerate(kept) if skills_page_dupe(k, s)), None
        )
        if dup_idx is None:
            kept.append(s)
            continue
        if _is_more_specific(s, kept[dup_idx]):
            log_deletion("_dedup_skills", "skills_page_dupe (less specific form)",
                         kept[dup_idx], superseded_by=s)
            kept[dup_idx] = s  # upgrade in place to the more-specific name
        else:
            log_deletion("_dedup_skills", "skills_page_dupe",
                         s, duplicate_of=kept[dup_idx])
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
        skills_page_dupe,
    )

    tailored_skills = [s for s in (tailored.skills or []) if isinstance(s, str) and s.strip()]
    # Master-profile skills are stored as objects ({"name": ..., "category": ...}), not bare
    # strings — the #192 guarantee below silently saw NONE of them when it filtered for `str`,
    # so JD-required skills the writer dropped (React/Node.js/JavaScript) were never re-added.
    # Extract the display name (dict → .name, or a plain string for legacy/mock data), keeping
    # the profile's own spelling verbatim — never fabricated. Mirrors gap_inference/choice_grounding.
    profile_skills: list[str] = []
    for s in profile_json.get("skills") or []:
        # ADR-061 clause 3: an unconfirmed skill cannot back a CV line — never
        # guarantee-restored, even when it maps to a JD-required term.
        if isinstance(s, dict) and s.get("status") == "unconfirmed":
            continue
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
    # re-added required skills appended. #386: the re-add check is the PAGE-scope
    # predicate — a vault spelling that would render as a visible duplicate of a
    # tag already on the page ('Lean Management' next to the writer's 'Lean') is
    # already covered, not missing (charter run 10 shipped six such clusters).
    pool = list(tailored_skills)
    for p in profile_skills:
        if _tier(p) == 0 and not any(skills_page_dupe(p, x) for x in pool):
            pool.append(p)

    # Collapse page-dupes (the newly re-added profile skills may twin a writer tag),
    # keeping the more-specific name — same shared page predicate as _dedup_skills.
    deduped: list[str] = []
    for s in pool:
        dup = next((i for i, k in enumerate(deduped) if skills_page_dupe(k, s)), None)
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

    * it page-dupes NO attested vault form (:func:`ats_audit.skills_page_dupe`
      over the profile's skill names AND each WorkEntry's ``technologies`` —
      #386: 'SAP MM' lives in the vault as a work-entry technology and in
      testimony, but the old skills[]-only tie was structurally blind to it and
      dropped a JD-named, vault-backed tag), AND
    * it page-dupes a JD-required/nice-to-have/keyword term or a Keyword Ledger
      concept/surface form (:func:`_jd_skill_terms`) -- i.e. it reads as an echo of
      the posting's own phrasing, not an independently-attested candidate skill.

    A skill that DOES tie to the vault is NEVER dropped — and is kept in the
    WRITER'S OWN WORDING. The former rename-toward-vault-phrasing step is
    retired (E049/ADR-067: the label is PROSE, owned by the writer in the
    output language; identity is what the tie establishes — renaming resurfaced
    the vault's mixed-language 'Arbeitssicherheit / Occupational Safety' label
    onto a German page, #386). A concept that ALSO exists as a genuine vault
    skill is correctly kept even though it happens to match the JD's own
    phrasing verbatim -- the fix is JD-echo-with-no-tie, never "matches the JD"
    alone.

    #386 pass-order disposition: runs BEFORE ``_tailor_skills_to_jd`` — the two
    passes were designed against each other: the cap ranked bare JD echoes as
    tier-0 and let them starve vault-confirmed tier-1 skills out of the page
    (ISO 9001, run 10), only for THIS pass to then delete 10 of the entries the
    cap had protected. Dropping echoes first lets the cap rank only entries
    that will actually ship. A dropped tag still cannot be re-added downstream:
    the #192 guarantee pool is vault skills only, and a dropped tag by
    definition has no vault tie. ``verified_missing_claimable`` still sees the
    true final document (this pass runs strictly before persistence).

    Pure; ``tailored`` is left unmutated. No-op (returns ``tailored`` unchanged, same
    object) when nothing is dropped.
    """
    from applire.services.ats_audit import skills_page_dupe

    original = [s for s in (tailored.skills or []) if isinstance(s, str) and s.strip()]
    if not original:
        return tailored

    vault_forms: list[str] = []
    for s in profile_json.get("skills") or []:
        # ADR-061 clause 3: an unconfirmed skill grants no "vault tie" either —
        # a tag that only matches an unconfirmed entry is not backed.
        if isinstance(s, dict) and s.get("status") == "unconfirmed":
            continue
        name = s.get("name") if isinstance(s, dict) else s
        if isinstance(name, str) and name.strip():
            vault_forms.append(name.strip())
    # #386: WorkEntry.technologies are attested vault data too — transcribed at
    # import, carried through reconciliation. A tag they back is not an echo.
    for w in profile_json.get("work_experience") or []:
        if not isinstance(w, dict):
            continue
        for t in w.get("technologies") or []:
            if isinstance(t, str) and t.strip():
                vault_forms.append(t.strip())

    required, nice, keyword = _jd_skill_terms(job_dict, keyword_ledger)
    jd_terms = required + nice + keyword

    def _vault_tied(skill: str) -> bool:
        return any(skills_page_dupe(skill, p) for p in vault_forms)

    def _is_jd_echo(skill: str) -> bool:
        return any(skills_page_dupe(skill, t) for t in jd_terms)

    kept: list[str] = [
        s for s in original if _vault_tied(s) or not _is_jd_echo(s)
    ]

    if kept == original:
        return tailored
    return tailored.model_copy(update={"skills": kept})


def _acronym_expansion_vault_match(mangled: str, vault_skills: list[str]) -> str | None:
    """Is ``mangled`` the vault's own skill name with a SINGLE token expanded/dropped?

    Tiramisu wave-6, blind hiring-panel run #6 (2026-07-26): the ADR-038 language
    pass treats a skill name as ordinary translatable prose. ``GxP`` is not on its
    proper-noun allow-list, so the model "translated" it by spelling the acronym
    out -- ``GxP Compliance & Computer System Validation`` shipped as ``Good
    Practice Compliance & Computer System Validation``.

    Why not just call :func:`ats_audit.skills_near_dupe` (the codebase's existing
    near-duplicate instrument, reused everywhere else in this file)? Checked
    against ground truth first (the fixer-pins-the-vector rule) and it returns
    False on all three pinned pairs -- the two extra tokens ("good", "practice")
    that replace the one dropped acronym token drag the Jaccard overlap down to
    0.25-0.57, under its 0.75 near-dupe bar, and the acronym breaks its containment
    check too (neither token set is a subset of the other). That bar is RIGHT for
    its own job -- deciding whether two skills are safe to auto-merge across a
    whole profile without conflating two genuinely different skills -- so it is
    not loosened here; a different, narrower rule is needed for a different job:
    restoring the spelling of an entry that both sides agree is the SAME skill.

    Reuses :func:`ats_audit.skill_tokens` (the shared tokenization primitive --
    NFKC, dash-fold, casefold, stopword/plural-fold) but applies its own strict
    correspondence rule, tuned to this exact failure shape rather than general
    similarity:

    * the vault name's tokens not present in ``mangled`` (``vault_only``) must be
      EXACTLY ONE token -- the presumed acronym -- never more (a bigger residual
      is not "one acronym expanded", it is a materially different name);
    * that one token must not be the vault name's ENTIRE content (an all-acronym
      vault skill, e.g. bare "GxP", has no other tokens to anchor the match on,
      so it is never restored via this path -- too ambiguous to risk);
    * ``mangled`` must carry extra tokens beyond the vault name's remainder
      (``mangled_only`` non-empty) -- something was substituted IN. A skill that
      merely drops the acronym with nothing added back is genuine containment
      and is already caught by ``skills_near_dupe`` (and thus already handled
      upstream by ``_drop_ungrounded_jd_echo_skills``'s existing vault-tie
      rename); duplicating that here would only risk a second, conflicting
      rewrite;
    * exactly ONE vault skill may satisfy the above -- if two candidates both
      qualify, the correspondence is ambiguous by definition and neither is
      returned (never guess between two vault originals).

    Returns the matching vault skill's exact string, or ``None`` when no
    candidate qualifies or more than one does.
    """
    from applire.services.ats_audit import skill_tokens

    mangled_tokens = skill_tokens(mangled)
    if not mangled_tokens:
        return None

    candidates: list[str] = []
    for vault_skill in vault_skills:
        vault_tokens = skill_tokens(vault_skill)
        if not vault_tokens:
            continue
        vault_only = vault_tokens - mangled_tokens
        mangled_only = mangled_tokens - vault_tokens
        if len(vault_only) != 1:
            continue
        if len(vault_only) >= len(vault_tokens):
            continue
        if not mangled_only:
            continue
        # E049 charter run 11 (2026-07-31), two false-positive shapes pinned on
        # live generation:
        # 1. The presumed acronym must be GONE from the writer's name, not
        #    riding inside a compound token — the writer's honest 'SAP PP/MM'
        #    is not a mangled spelling of the vault's 'SAP PP' ('pp' ⊂
        #    'pp/mm'); rewriting deleted the MM half.
        # 2. An EXPANSION replaces one acronym with MULTIPLE words ('GxP' →
        #    'Good Practice'). A single-token swap is a SIBLING code ('SAP MM'
        #    vs 'SAP PP' — MM is not PP spelled out); rewriting renamed a real
        #    module into its neighbour and the dedup guard then deleted it.
        (acronym,) = vault_only
        if any(acronym in m for m in mangled_only):
            continue
        if len(mangled_only) < 2:
            continue
        candidates.append(vault_skill)

    if len(candidates) == 1:
        return candidates[0]
    return None  # zero or ambiguous (>= 2) matches -- never guess


def _restore_skill_spelling(tailored: TailoredCVData, profile_json: dict | None) -> TailoredCVData:
    """Restore any tailored skill name the LLM chain has mangled back to the vault's
    exact string (Tiramisu wave-6, blind hiring-panel run #6, 2026-07-26).

    Ground truth: three of the tailored CV's skill entries had their vault-attested
    ``GxP`` acronym expanded to "Good Practice" by the ADR-038 language pass
    (``_review_cv_language``, chain_id ``cv_language``) -- see
    ``_acronym_expansion_vault_match`` for how that vector was pinned against the
    codebase's existing near-duplicate instrument before this guard was written.
    The Oracle then correctly flagged all three as ``unbacked`` (no vault evidence
    for "Good Practice Environments" -- true, that string does not exist in the
    vault), and a blind hiring panel quoted the mangled names back as evidence the
    document was untrustworthy. Prompt wording alone will not hold against every
    provider/temperature -- this deterministic post-pass is the actual protection.

    Deliberately conservative, per this fix's guardrails:

    * an EXACT vault match (post NFKC/casefold/whitespace normalisation via
      ``ats_audit._norm`` -- the same fold ``_dedup_skills``/``_tailor_skills_to_jd``
      already rely on) is always restored to the vault's own casing/spacing --
      formatting noise, never ambiguous;
    * anything else is only rewritten when
      :func:`_acronym_expansion_vault_match` finds a SINGLE, unambiguous vault
      original -- see that function's docstring for the exact bar and why the
      existing ``skills_near_dupe`` instrument does not (and should not) clear it
      for this failure shape;
    * a skill with no such correspondence is left EXACTLY as the writer produced
      it and logged -- never guessed at, never deleted;
    * a vault skill the tailoring step legitimately chose not to include is never
      re-added -- this pass only ever rewrites the spelling of an entry that is
      already present, never adds or removes one;
    * selection and order from every upstream pass (``_tailor_skills_to_jd``'s
      subsetting/cap, ``_drop_ungrounded_jd_echo_skills``'s drops) are preserved
      exactly -- entries are rewritten strictly in place, never reordered.

    Runs LAST in the skills pipeline, after ``_drop_ungrounded_jd_echo_skills``, so
    it restores the spelling of the FINAL skill list, not an intermediate one that
    a later pass might still drop or rename.

    Certifications carry the same truthfulness requirement but need no equivalent
    guard here: ``_apply_certifications`` already bypasses the LLM chains entirely
    and copies ``profile_json["certifications"]`` in verbatim as a pure passthrough
    (PQ F7 / ADR-040) -- there is no LLM-authored certification name for any chain
    to mangle in the first place.

    Pure; ``tailored`` is left unmutated. No-op (returns ``tailored`` unchanged,
    same object) when nothing needs restoring. Tolerates ``profile_json`` being
    ``None``/malformed and ``tailored.skills`` being empty.
    """
    from applire.services.ats_audit import _norm

    original = list(tailored.skills or [])
    if not original:
        return tailored

    vault_skills: list[str] = []
    for entry in (profile_json or {}).get("skills") or []:
        # ADR-061 clause 3: an unconfirmed skill is not a restoration target —
        # spelling a surviving tag toward an unclaimable entry still implies
        # the vault backs it.
        if isinstance(entry, dict) and entry.get("status") == "unconfirmed":
            continue
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            vault_skills.append(name.strip())
    if not vault_skills:
        return tailored

    vault_by_norm: dict[str, str] = {}
    for v in vault_skills:
        vault_by_norm.setdefault(_norm(v), v)

    restored: list[str] = []
    changed = False
    for skill in original:
        if not isinstance(skill, str) or not skill.strip():
            restored.append(skill)
            continue

        exact = vault_by_norm.get(_norm(skill))
        if exact is not None:
            # E049 charter run 11: a restoration must NEVER introduce a page
            # duplicate — if the vault form is already on the list (another
            # entry restored to it, or the #192 guarantee re-added it), this
            # entry collapses into it instead of appearing twice.
            if any(_norm(exact) == _norm(r) for r in restored):
                changed = True
                logger.info(
                    "skill spelling guard: %r restores to %r which is already "
                    "listed — collapsed (no duplicate introduced)", skill, exact,
                )
                continue
            if exact != skill:
                changed = True
            restored.append(exact)
            continue

        match = _acronym_expansion_vault_match(skill, vault_skills)
        if match is not None:
            if any(_norm(match) == _norm(r) for r in restored):
                changed = True
                logger.info(
                    "skill spelling guard: %r restores to %r which is already "
                    "listed — collapsed (no duplicate introduced)", skill, match,
                )
                continue
            changed = True
            restored.append(match)
            continue

        logger.info(
            "skill spelling guard: %r has no unambiguous vault original -- left as-is",
            skill,
        )
        restored.append(skill)

    if not changed:
        return tailored
    return tailored.model_copy(update={"skills": restored})


def _restore_narrative_named_skills(
    tailored: TailoredCVData,
    profile_json: dict | None,
    keyword_ledger: list[dict] | None,
) -> TailoredCVData:
    """#376 — a skill named in a generated bullet must not be missing from the
    generated skills list.

    Ground truth (ADR-064 charter run, section 4 finding F3, 2026-07-29): a
    tailored CV's own work-experience bullet reads "Tägliche Arbeit mit SAP PP
    und SAP MM (Disposition und Bestellanforderungen)" — both module names
    spelled out in full — while the Kenntnisse (skills) section lists only
    "SAP PP". "SAP MM" is absent from the one field a recruiter and every ATS
    keyword pass reads first, in the very document that names it twice.

    STRUCTURAL fix only (ADR-062 clause 1: a fact, not a judgement): for a
    name already KNOWN to be true of this candidate — a vault ``Skill`` row
    (unconfirmed rows excluded, ADR-061 clause 3) or a CLAIMABLE Keyword
    Ledger concept/surface form (never a gap/denied one — that would be a
    truthfulness violation, not a correction) — literal presence in the
    tailored NARRATIVE (:func:`keyword_ledger._tailored_narrative_texts`,
    work-history + nested project bullets) is a plain substring fact. When a
    known name is present there but absent from the skills list (no near-dupe
    either, :func:`ats_audit.skills_near_dupe`), it is added.

    **#219 — "known true" is resolved by the Oracle's own predicate, not by a
    second one.** A ledger row's ``claimable`` flag is the gap-analysis LLM's
    classification (``keyword_ledger.build_keyword_ledger``: ``claimable =
    status in {direct, partial}``); no deterministic vault tie is computed
    anywhere on that path. The Oracle audits the resulting chip with
    :func:`applire.services.oracle.matchers.grounding.ground_skill_claim`
    (``surface_present`` over the vault's evidence units, then
    ``skills_near_dupe`` over its skill names). Two resolutions of one
    question, so the generator could put a name on the page that its own
    self-audit then rejected: the 2026-07-21 edge UAT (build 59f891f, finding
    F5) shipped the chip "Technical leadership" — the ledger's concept string
    — against a vault whose skill is "Team Leadership", and the Oracle marked
    it ``unbacked`` in the generator's own output.

    The two are converged here on the SELECTION side (ADR-066 clause 2: one
    logical operation, one implementation) by calling ``ground_skill_claim``
    itself before a name is added — never by loosening the Oracle. Loosening
    would be the wrong direction twice over: ``skills_near_dupe`` deliberately
    refuses this shape (``tests/unit/test_ats_audit.py``'s
    ``_MUST_NOT_MERGE_PAIRS`` pins "Team Leadership"/"Project Leadership" as a
    MUST-NOT-merge pair — same head noun, different modifier, genuinely
    different skills), and ``test_oracle_matchers.py``'s
    ``test_ground_skill_claim_strategic_planning_stays_unbacked`` pins that a
    ledger verdict resting on LLM semantic adjacency is *correctly* left
    unbacked by the Oracle's deterministic contract. A vault ``Skill`` row
    grounds trivially under the same predicate (its own name IS a ``skills[i]``
    evidence unit), so #376's reported SAP case is untouched; only the
    adjacency-only ledger names are held back.

    Deliberately does NOT resolve an elided compound ("SAP PP und MM" implying
    "SAP MM") — reading what an elided sentence MEANS is a judgement under
    ADR-062 clause 1, and #376's own cover-letter instance is exactly that
    shape ("Täglich arbeite ich mit SAP PP und MM"). The CV's own bullet in
    the reported case spells both names out in full, so the literal-substring
    fact is enough for THIS half; the elided form intentionally does not
    trigger this guard and is left to a model-side follow-up (ADR-061
    precedent: ``services/profile/reconcile/stance.py`` LLM stance
    adjudication, not a matcher).

    Never invents a name: a candidate that never appears anywhere in the
    tailored document is never added (this guard fixes a self-contradiction,
    it never re-adds a vault skill the writer legitimately chose to omit).
    Appends in place — never reorders or removes an existing entry. Pure;
    ``tailored`` is left unmutated. No-op (returns ``tailored`` unchanged,
    same object) when nothing needs adding. Tolerates ``profile_json``/
    ``keyword_ledger`` being ``None``/malformed.

    Run LAST in the skills pipeline (after ``_restore_skill_spelling``), on
    the FINAL skills list and the FINAL narrative text, so it never chases a
    spelling a later pass would still change.
    """
    from applire.services.ats_audit import _norm, skills_page_dupe, surface_present
    from applire.services.keyword_ledger import (
        _tailored_narrative_texts,
        claimable_surface_form_groups,
    )
    from applire.services.oracle.matchers.grounding import ground_skill_claim
    from applire.services.oracle.matchers.vault import build_vault_index

    existing = [s for s in (tailored.skills or []) if isinstance(s, str) and s.strip()]

    # #386 (E049 clause-6 disposition): candidates are GROUPS — one group per
    # competence, at most ONE page entry added per group. The flattened form list
    # made every sibling surface form of one ledger row an independent candidate,
    # and 'Dreischichtbetrieb' + 'Schichtbetrieb' + 'Lean' + 'Kaizen' (all sibling
    # forms of two rows) landed as four separate tags on one delivered page.
    groups: list[list[str]] = []
    seen_norm: set[str] = set()

    for entry in (profile_json or {}).get("skills") or []:
        # ADR-061 clause 3: an unconfirmed skill cannot back a CV line.
        if isinstance(entry, dict) and entry.get("status") == "unconfirmed":
            continue
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            n = _norm(name)
            if n and n not in seen_norm:
                seen_norm.add(n)
                groups.append([name.strip()])

    for group in claimable_surface_form_groups(keyword_ledger):
        fresh = [f.strip() for f in group if isinstance(f, str) and f.strip()]
        if fresh:
            groups.append(fresh)

    if not groups:
        return tailored

    narrative_norm = _norm(" ".join(_tailored_narrative_texts(tailored.model_dump(mode="json"))))
    if not narrative_norm:
        return tailored

    def _covered(name: str) -> bool:
        # PAGE-scope coverage (#386): a form whose addition would render as a
        # visible duplicate of an entry already on the list is covered, not missing.
        return any(_norm(name) == _norm(s) or skills_page_dupe(name, s) for s in existing)

    # #219: THE vault-backing predicate — the same one the Oracle audits the
    # finished page with (ADR-066 clause 2). A name this cannot ground is a name
    # the document's own truthfulness report would mark `unbacked`, so it is
    # never added, however the ledger classified it.
    vault_index = build_vault_index(profile_json or {})

    def _oracle_backed(name: str) -> bool:
        return ground_skill_claim(name, vault_index) is not None

    to_add: list[str] = []
    for group in groups:
        if any(_covered(f) for f in group):
            continue  # the competence is already on the page in some form
        narrated = [f for f in group if surface_present(f, narrative_norm)]
        hit = next((f for f in narrated if _oracle_backed(f)), None)
        if hit is None:
            if narrated:
                # Never a silent hold-back: this is the #219 case, and the
                # ledger row that authorised the name is what to look at.
                logger.info(
                    "skills-list gap guard (#376): %r is narrated but no form of "
                    "it grounds against the vault (#219, ground_skill_claim) — "
                    "not added; the Oracle would audit the chip unbacked",
                    narrated[0],
                )
            continue
        to_add.append(hit)
        existing = existing + [hit]  # later groups see this one as covered

    if not to_add:
        return tailored

    for name in to_add:
        logger.info(
            "skills-list gap guard (#376): %r named in a narrative bullet but absent "
            "from the skills list — added",
            name,
        )
    return tailored.model_copy(update={"skills": list(tailored.skills or []) + to_add})


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
_jinja_env = build_template_env(_TEMPLATES_DIR)

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
        # ADR-060 clause 6: the Pass A verdict is data on the status surface,
        # both doors (REST poller and MCP get_cv_status serialize this model).
        critic_report=record.critic_report,
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
# Render-context guard (#312)
# ---------------------------------------------------------------------------


def project_has_content(project: Any) -> bool:
    """True when a project carries at least one non-blank bullet.

    The single predicate behind #312 (ADR-066: one logical operation, one
    implementation). ``bullets == []`` and ``bullets == ["  "]`` are the same
    thing on the page — a heading with nothing under it.
    """
    bullets = project.bullets if hasattr(project, "bullets") else (project or {}).get("bullets")
    return any(isinstance(b, str) and b.strip() for b in (bullets or []))


def strip_empty_projects(tailored: TailoredCVData) -> TailoredCVData:
    """Remove every bullet-less project from the RENDER context (#312).

    Charter run #7 delivered a CV whose ``PROJEKTE`` section held a bold
    ``SAP-Rollout bei Rasselstein`` heading with zero bullets under it, styled
    exactly like the populated heading above — a section that reads as having
    failed to render, on a document whose whole job is to survive a skeptical
    scan.

    All seven templates gate only the ``<ul>`` (``{% if project.bullets %}``)
    and emit ``project.name`` unconditionally, in both render paths (nested
    under a position, and the standalone ``cv.projects`` section). Guarding
    fourteen Jinja sites would be fourteen implementations of one rule, and the
    next template would ship without it; guarding the context guards every
    template, present and future, at one site. Removing the ENTRY (rather than
    blanking it) also collapses the ``Projekte`` sub-label and the standalone
    section title, which are gated on the list being non-empty.

    This is the guard the issue calls "the one that makes it impossible to
    ship": the generation-side guards in ``_nest_projects`` /
    ``_suppress_duplicate_project_bullets`` do not run on the ADR-054 agent
    door, which persists caller-authored content verbatim.

    Returns a copy; ``tailored`` is left unmutated (the caller may still be
    holding the record's own validated data).
    """
    work_history = [
        w.model_copy(update={"projects": [p for p in w.projects if project_has_content(p)]})
        if any(not project_has_content(p) for p in w.projects)
        else w
        for w in tailored.work_history
    ]
    return tailored.model_copy(update={
        "work_history": work_history,
        "projects": [p for p in tailored.projects if project_has_content(p)],
    })


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
    # #312: never hand a template a project with nothing under its heading.
    tailored = strip_empty_projects(tailored)

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
            # E049 (#383 prompt-side half): critical_gaps are no longer fed to the
            # writer — the CRITICAL GAPS prompt block contradicted the "a CV is not
            # the place to disclose a gap" rule on every call. Gap handling is the
            # Keyword Ledger's job (ADR-048).
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
            # ADR-061 clause 3: neither the writer LLM nor any deterministic pass
            # below (certifications passthrough, skill-restoration pools) may see
            # an unconfirmed vault entry — it cannot back a CV line. The
            # candidate's own persisted profile is untouched; this is a filtered
            # COPY used for generation only.
            from applire.services.profile.reconcile.stance import exclude_unconfirmed
            profile_json = exclude_unconfirmed(profile_json)

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

            # STATED LIMITS: the candidate's persisted denial statements, verbatim
            # (ProfileMetadata.denied_concepts). Threaded into the writer prompt(s) below
            # so a CV skill tag or summary line never contradicts something the candidate
            # explicitly said they cannot claim. Facts only — this deliberately does NOT
            # decide which claimable concept each limit bears on; that pairing used to be
            # `find_scoped_boundaries` and it was wrong on real data in the one direction
            # that matters (see services/cross_document.collect_stated_limits).
            from applire.services.cross_document import (
                collect_stated_limits,
                render_stated_limits_block,
            )

            denied_concepts = (profile_json.get("metadata") or {}).get("denied_concepts") or []
            stated_limits_block = render_stated_limits_block(
                collect_stated_limits(denied_concepts)
            )

            # ADR-070 clause 2: the candidate's own scale evidence for partial scope
            # entries (bar.attested + typed values) — the ONLY channel scope material
            # takes into a document (scope entries are excluded from the ledger block
            # by is_scope_entry). Empty → adds nothing.
            from applire.services.scope_requirements import render_scope_positioning_block

            scope_positioning_block = render_scope_positioning_block(
                keyword_ledger, resolve_jd_language(job)
            ) or None

            # #303 (#271's CV half): the strongest-vault-evidence digest — for
            # each claimable ledger concept, the vault's OWN sentence that
            # answers it, verbatim, with the entry that owns it. The letter
            # chain has had this since #271; the CV chain never did, and its
            # only concept→evidence pointer was the ledger's `evidence` field
            # — the gap classifier's free-text rationale, which quotes no vault
            # text and names no owner. That asymmetry is why the letter kept
            # naming figures and daily-use sentences the CV had reduced to a
            # bare skills keyword (charter runs #7/13/17/18), which every blind
            # panel read as `aufgeblasen`. Same selector, same items as the
            # letter (ADR-066); only the instruction wording differs.
            #
            # This OFFERS evidence to the writer. It gates nothing, deletes
            # nothing, and demands no surface form appear anywhere — the
            # 2026-07-30 revert (ADR-060 amended; #377) is the standing reason
            # a presence PREDICATE may not be built here.
            from applire.services.jd_excerpt import build_jd_excerpt
            from applire.services.vault_evidence import (
                render_vault_evidence_block,
                select_vault_evidence,
            )

            # Fail-safe by construction: this block only ADDS context to a
            # prompt, so losing it degrades quality and nothing else. It must
            # never become a new way for CV generation to fail — the same
            # boundary guarantee the ADR-071 attribution round below is given,
            # enforced here rather than trusted to the callee.
            vault_evidence_items: list = []
            vault_evidence_block: str | None = None
            try:
                jd_raw = job.raw_text if isinstance(job.raw_text, str) else ""
                vault_evidence_items = select_vault_evidence(
                    keyword_ledger,
                    build_jd_excerpt(jd_raw),
                    # Already `exclude_unconfirmed`-filtered above (ADR-061
                    # clause 3) — an unconfirmed entry cannot back a CV line
                    # and must not be offered as evidence either.
                    profile_json,
                    # #271: the posting's own leadership-vs-hands-on weighting,
                    # extracted at analyse time. Drives rule 3's trigger, its
                    # sub-cap and the quote the writer positions against; None
                    # on a pre-migration-0056 row falls back to the legacy JD
                    # marker check inside the selector. Same selector, same
                    # facet as the letter (ADR-066).
                    leadership_emphasis=getattr(job, "leadership_emphasis", None),
                )
                vault_evidence_block = (
                    render_vault_evidence_block(vault_evidence_items, chain="cv") or None
                )
            except Exception:
                logger.exception(
                    "strongest-vault-evidence selection failed for CV %s — the writer "
                    "runs without the digest (#303)", cv_id,
                )
                vault_evidence_items = []

            provider: LLMProvider = get_provider()
            # Single call on the fast path; segmented (outline-then-expand) as the fallback
            # on truncation/timeout or a known-small cap (ADR-047 §1/§2 / US189).
            # E049/ADR-067: both paths return the PROSE shape (summary / id-keyed work /
            # skills) — the vault facts are joined only after both LLM review chains.
            prose_draft: dict = await _tailor_cv_with_fallback(
                job_dict,
                profile_json,
                keyword_gaps,
                output_language=resolve_jd_language(job),
                provider=provider,
                keyword_ledger=keyword_ledger,
                budget=budget,
                stated_limits_block=stated_limits_block,
                scope_positioning_block=scope_positioning_block,
                vault_evidence_block=vault_evidence_block,
                vault_evidence_items=vault_evidence_items,
            )

            source_material = _json.dumps(profile_json, ensure_ascii=False, indent=2)
            # #277: fold the SAME scoped-boundary block into the reviewer/retry source —
            # mirrors the ledger_block fold immediately below (US202+US213 precedent) —
            # so a review-loop retry (_build_cv_retry_prompt reads `source` as the
            # candidate's ground truth) has the vault's own scoped wording available to
            # correct a bare tag back to the scoped form, without adding a new reviewer
            # check or a new LLM pass.
            if stated_limits_block:
                source_material = f"{source_material}\n\n{stated_limits_block}"
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

            prose_draft = await review_and_refine(
                source=source_material,
                draft=prose_draft,
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

            # ADR-071 clause 3: the Oracle's `misattributed` verdict gains a
            # generation-side consumer. The audit is DETERMINISTIC-ONLY (no
            # provider, no entailment) — the attribution red flag is an
            # id-anchored comparison and needs no model. When it fires, at most
            # ONE targeted cv_tailoring round asks the writer to RE-PLACE the
            # bullet: never a strip, never a gate (see the module docstring).
            #
            # Runs HERE — after the review loop settles, before the language
            # pass — for two reasons. The persisted self-audit in
            # _update_ats_report is far too late (it runs after the whole
            # deterministic tail, after `status = ready` and after
            # `tailored_data` is written, with no writer left to ask). And
            # placing it before _review_cv_language keeps that pass's "this is
            # the LAST writer" property intact, so a relocated bullet is still
            # language-checked and still watched by the US213 coverage gate.
            #
            # The audit needs the ASSEMBLED shape (claims are stamped with the
            # rendered position's id), so a throwaway join is built for it. That
            # join is pure and fail-closed on an unknown id; a failure here must
            # never become a new way for generation to fail, so it only skips
            # the round — the real assembly below reports the same error.
            try:
                from applire.services.attribution_round import run_attribution_round
                from applire.services.oracle.selfaudit import build_self_audit_report

                audit_view = assemble_tailored_cv(prose_draft, profile_json)
                attribution_report = await build_self_audit_report(
                    profile_json, tailored_data=audit_view,
                )
                # Inside the try deliberately. ``run_attribution_round`` is
                # written never to raise, but "never raises" asserted only by
                # one function's own completeness is not a defence — one
                # unguarded line added to it later would otherwise become a
                # hard failure of CV generation. ADR-052 §5 says this may never
                # gate delivery, so the guarantee is enforced at the boundary.
                prose_draft = await run_attribution_round(
                    prose_draft,
                    report=attribution_report,
                    profile_json=profile_json,
                    source_material=source_material,
                    provider=provider,
                )
            except Exception:
                logger.exception(
                    "The ADR-071 clause 3 attribution round failed for CV %s — "
                    "skipped; generation continues with the settled draft", cv_id,
                )

            # ADR-038 enforcement: ensure skill tags + prose (incl. project bullets)
            # are all in the target-job language (the directive alone leaks
            # discipline-skill phrases — #1). E049/ADR-067: runs on the PROSE shape,
            # BEFORE assembly — an LLM re-emission can therefore no longer mutate an
            # employer/date or drop a work-entry id (the #303/GxP custody class).
            # Vault facts joined below are verbatim by design and are not re-worded.
            # Carries the ledger: this pass is the LAST writer, so the US213 coverage
            # gate must also watch its rewording (#122 follow-up).
            prose_draft = await _review_cv_language(
                prose_draft, resolve_jd_language(job), provider,
                keyword_ledger=keyword_ledger,
            )

            # E049/ADR-067 clauses 2–3: THE deterministic join — prose onto vault
            # facts (contact, employer/role/dates by id, education, languages).
            # Fail-closed on an unknown id; shared by both generation paths.
            tailored = TailoredCVData.model_validate(
                assemble_tailored_cv(prose_draft, profile_json)
            )

            # US187: deterministically nest source projects under their parent
            # position (or the standalone list). The LLM tailors prose; code
            # disposes. Runs after assembly (it matches on the joined company/role
            # identity). The nested copies are verbatim vault facts — like
            # education, they are carried in the vault's own language (ADR-067:
            # transcription is not re-worded by any LLM pass).
            tailored = _nest_projects(tailored, profile_json)

            # PQ F7: deterministically copy the profile's certifications verbatim
            # (ADR-040 truthfulness) — never routed through the LLM. Covers both the
            # single-call and segmented paths, since both converge here.
            tailored = _apply_certifications(tailored, profile_json)

            # #328: deterministically copy each work entry's quantified role facts
            # (team_size / budget_managed / industry_context) from the vault onto the
            # matching tailored entry, for rendering as document furniture (ADR-062
            # clause 1) — bypassing prose (and the writer LLM) entirely. Matched by
            # the WorkEntry.id identity assemble_tailored_cv establishes
            # structurally, never by company-name string. Uses the SORTED
            # profile_json (still bound here; the photo step below rebinds the
            # name to the raw profile dict).
            tailored = _apply_role_facts(tailored, profile_json)

            # #234 (Tiramisu founder-acceptance F1/F2): deterministically restore any
            # verbatim vault bullet that carries a claimable Keyword Ledger concept the
            # writer's draft dropped entirely. Keyed by the same profile WorkEntry.id
            # the budget uses (structural since assemble_tailored_cv). Uses the SORTED
            # profile_json (still bound here; the photo step below rebinds the name to
            # the raw profile dict).
            tailored = _restore_ledger_bullets(tailored, profile_json, keyword_ledger, budget)

            # #261 (run-4 blind hiring-panel finding): deterministically prefer a
            # MEASURED outcome over a bare target/projection for the same initiative
            # (owner-scoped via the #196/#244 attribution machinery). MUST run after
            # _restore_ledger_bullets so a restored vault bullet is also covered.
            # Uses the SORTED profile_json (still bound here; the photo step below
            # rebinds the name to the raw profile dict).
            tailored = _prefer_measured_outcomes(
                tailored, profile_json, resolve_jd_language(job)
            )

            # #172: collapse near-duplicate skill tags (the shared ats_audit
            # predicate) so the CV is clean even when the master profile still
            # carries twins. After the language pass, which rewords the tags.
            tailored = _dedup_skills(tailored)

            # #250 (Tiramisu founder-acceptance blind-panel finding): drop bare skill
            # tags that are JD/ledger-concept echoes with no deterministic vault tie
            # (both blind reviewers independently flagged these as keyword-stuffing).
            # #386 reorder: runs BEFORE #192's cap — the cap used to rank doomed
            # echoes as tier-0 and starve vault-confirmed skills (ISO 9001) out of
            # the page, only for this pass to then delete the very entries the cap
            # protected. A dropped tag cannot be re-added below: the #192 guarantee
            # pool is vault skills only, and dropped ⇒ no vault tie.
            tailored = _drop_ungrounded_jd_echo_skills(
                tailored, profile_json, job_dict, keyword_ledger
            )

            # #192: present a prioritised, JD-relevant SUBSET of the candidate's skills
            # instead of the whole master profile. Deterministic, downstream of the LLM +
            # language pass (so it ranks the final target-language tags): guarantees the
            # JD-required skills the candidate actually has, drops no-relevance tags over
            # the cap, and never invents a skill. Uses the SORTED profile_json (still bound
            # here — the photo step below rebinds `profile_json` to the raw profile dict).
            tailored = _tailor_skills_to_jd(
                tailored, profile_json, job_dict, keyword_ledger
            )

            # Tiramisu wave-6 (blind hiring-panel run #6, 2026-07-26): restore any
            # skill name the ADR-038 language pass mangled (e.g. "GxP" expanded to
            # "Good Practice") back to the vault's exact string. MUST run before the
            # #376 guard just below, so that guard's near-dupe check compares against
            # the FINAL corrected spelling, not an intermediate mangled one. Only ever
            # rewrites a name already present; never adds or removes an entry.
            tailored = _restore_skill_spelling(tailored, profile_json)

            # #376 (ADR-064 charter run, section 4 finding F3): a skill named in a
            # generated bullet ("SAP PP und SAP MM") but missing from the generated
            # skills list ("SAP PP" only) -- the document contradicting itself. MUST
            # run LAST in the skills pipeline -- after every selection/cap/drop/
            # spelling pass above, and BEFORE tailored_data/the ATS audit are
            # persisted below, so the audit (and any human reader) sees the final,
            # self-consistent document. Only ever ADDS a name already known-true and
            # already narrated; never invents, reorders, or removes an entry.
            tailored = _restore_narrative_named_skills(tailored, profile_json, keyword_ledger)

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
        # #249 run-4: the vault's own literal text backs the shared-predicate guard —
        # a keyword the Oracle would ground against the vault verbatim must never land
        # in present_unsupported, so the two panels cannot contradict each other.
        from applire.services.keyword_ledger import profile_literal_corpus

        profile_row = await db.get(MasterProfile, record.profile_id)
        vault_text_norm = (
            profile_literal_corpus(profile_row.profile_json if profile_row else None)
            or None
        )
        record.ats_report = _audit_cv_text(
            text,
            tailored,
            list(job.keywords or []) if job else [],
            ledger,
            page_count=count,
            target=target,
            region=region,
            condensation_exhausted=condensation_exhausted,
            vault_text_norm=vault_text_norm,
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
        # ADR-068 clause 7: the JD-resolved OUTPUT language (ADR-038,
        # ``resolve_jd_language`` — the same value the writer/reviewer chain
        # itself generated this CV in, see e.g. line ~2087's
        # ``output_language=resolve_jd_language(job)``) is this document's
        # own language for the cross-language judgement seam. A job-less
        # audit (job row missing/deleted) leaves it ``None`` — the seam then
        # stays off, fail-open, exactly like the pre-ADR-068 report.
        judgement_job = await db.get(JobAnalysis, record.job_analysis_id)
        record.truthfulness_report = await build_self_audit_report(
            profile.profile_json if profile else {},
            tailored_data=audited.model_dump(mode="json"),
            provider=get_provider(),
            document_language=(
                resolve_jd_language(judgement_job) if judgement_job else None
            ),
        )
    except Exception:
        logger.exception(
            "Truthfulness self-audit failed for CV %s — report left NULL", record.id
        )
        record.truthfulness_report = None
    # ADR-060 Pass A (third amendment, 2026-07-31 / ADR-067 clause 5): the
    # outcome critic reads the ASSEMBLED CV — post-join, post-every-pass,
    # overrides applied — and rides the same commit as the two reports above,
    # so "ready implies critic verdict available" holds before the poller can
    # see a terminal status. A SEPARATE try block, deliberately independent
    # of the other blocks' locals: an ATS/truthfulness failure must never
    # take the critic down, or vice versa. Advisory-only, never gates
    # delivery (see run_pass_a's own short-circuits, SF-CRITIC.1/.8).
    try:
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.services.jd_excerpt import build_jd_excerpt
        from applire.services.outcome_critic import run_pass_a

        job_row = await db.get(JobAnalysis, record.job_analysis_id)
        assembled = apply_overrides_to_tailored(
            TailoredCVData.model_validate(record.tailored_data),
            record.content_snapshot,
            record.section_overrides,
        )
        critic_report = await run_pass_a(
            cv_tailored=assembled.model_dump(mode="json"),
            job_role_title=job_row.role_title if job_row else None,
            jd_excerpt=build_jd_excerpt(job_row.raw_text) if job_row else None,
            provider=get_provider(),
        )
        record.critic_report = critic_report.model_dump(mode="json")
    except Exception:
        logger.exception(
            "Outcome critic Pass A failed for CV %s — critic_report left NULL",
            record.id,
        )
        record.critic_report = None
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


async def get_cv_critic_report(
    cv_id: uuid.UUID, db: AsyncSession
) -> "OutcomeCriticReportResponse":
    """Return the persisted Pass A outcome-critic report for a CV (ADR-060
    third amendment / E049 49.6) — the CV-side mirror of
    ``get_cover_letter_critic_report`` (ADR-066: one implementation shape per
    capability, one per document owner).

    Raises LookupError if the CV is not found (→ 404 in the router). A
    malformed stored report degrades to report=null, never a 500.
    """
    from applire.schemas.outcome_critic import (
        OutcomeCriticReport,
        OutcomeCriticReportResponse,
    )

    record = await _load_cv(cv_id, db)
    report = None
    if record.critic_report:
        try:
            report = OutcomeCriticReport.model_validate(record.critic_report)
        except Exception:
            logger.warning(
                "Stored critic report for CV %s is malformed — returning report=null",
                record.id,
            )
            report = None
    return OutcomeCriticReportResponse(
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
