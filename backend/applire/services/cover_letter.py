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

"""Cover letter generation service — Sprint 25

Mirrors services/cv.py:
  generate_cover_letter:
    Create GeneratedCoverLetter record with status='pending'.
    Update FlowSession.generated_cover_letter_id.
    Enqueue _render_cover_letter_background via BackgroundTasks.
    Return immediately — caller polls GET /api/cover-letter/{id}/status.

  _render_cover_letter_background:
    LLM + Jinja2 + Playwright — runs outside request lifecycle.
    Updates status: pending → generating → ready | failed.
    Creates its own DB session.
"""

import copy
import json
import logging
import uuid
from datetime import date, timezone
from pathlib import Path

from fastapi import BackgroundTasks
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.db.session import AsyncSessionLocal
from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.constants import CV_GENERATION_MAX_TOKENS, LLM_REVIEW_MAX_RETRIES
from applire.prompts.cover_letter import SYSTEM_PROMPT, build_cover_letter_prompt
from applire.prompts.review_cover_letter import (
    COVER_LETTER_REFINEMENT_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    build_retry_prompt,
    build_review_prompt,
)
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.services.reviewer import review_and_refine
from applire.schemas.cover_letter import (
    CoverLetterGenerateRequest,
    CoverLetterGenerateResponse,
    CoverLetterStatusResponse,
)
from applire.utils.language_detection import resolve_jd_language
from applire.utils.letter_date import format_letter_date
from applire.utils.recipient_extraction import extract_recipient_from_jd

logger = logging.getLogger(__name__)

_TEMPLATE_FILES: dict[str, str] = {
    "classic_german": "lebenslauf_letter.html.j2",
    "modern_swiss": "modern_swiss_letter.html.j2",
    "executive": "executive_letter.html.j2",
    "tech_developer": "tech_developer_letter.html.j2",
    "creative_sidebar": "creative_sidebar_letter.html.j2",
    "academic": "academic_letter.html.j2",
    "compact_pro": "compact_pro_letter.html.j2",
}

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


async def generate_cover_letter(
    request: CoverLetterGenerateRequest,
    db: AsyncSession,
    provider: LLMProvider,
    background_tasks: BackgroundTasks,
    base_url: str,
) -> CoverLetterGenerateResponse:
    """Create a pending GeneratedCoverLetter and enqueue the background render."""
    # Resolve flow session for this job
    flow_result = await db.execute(
        select(FlowSession).where(
            FlowSession.job_id == request.job_id,
            FlowSession.deleted_at.is_(None),
        )
    )
    flow = flow_result.scalar_one_or_none()
    if flow is None:
        raise LookupError(f"No flow session found for job {request.job_id}")

    # Resolve the active CV (for template + color_profile_id)
    cv: GeneratedCV | None = None
    template = "classic_german"
    color_profile_id: uuid.UUID | None = None
    if flow.generated_cv_id is not None:
        cv_result = await db.execute(
            select(GeneratedCV).where(GeneratedCV.id == flow.generated_cv_id)
        )
        cv = cv_result.scalar_one_or_none()
        if cv is not None:
            template = cv.template
            color_profile_id = cv.color_profile_id

    # Resolve profile
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError("No profile found — complete the interview step first")

    # Build pre_gen_inputs for storage
    pre_gen_inputs = {
        "recipient_name": request.recipient_name,
        "recipient_company": request.recipient_company,
        "salary": request.salary,
        "availability": request.availability,
        "motivation": request.motivation,
        "tone": request.tone,
    }

    # Create the record — flush immediately so cl.id is populated before we
    # assign it to flow.generated_cover_letter_id (SQLAlchemy default=uuid.uuid4
    # is applied at flush time, not at object construction time).
    cl = GeneratedCoverLetter(
        job_analysis_id=request.job_id,
        profile_id=profile.id,
        template=template,
        letter_data={},
        pre_gen_inputs=pre_gen_inputs,
        color_profile_id=color_profile_id,
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.flush()  # assigns cl.id

    # Update FlowSession pointer
    flow.generated_cover_letter_id = cl.id

    await db.commit()
    await db.refresh(cl)

    # Enqueue background render
    background_tasks.add_task(
        _render_cover_letter_background,
        cl_id=cl.id,
        cv_id=flow.generated_cv_id,
        job_id=request.job_id,
    )

    return CoverLetterGenerateResponse(
        cover_letter_id=cl.id,
        status=CoverLetterStatus.pending,
        html_url=f"{base_url}/api/cover-letter/{cl.id}/html",
        pdf_url=f"{base_url}/api/cover-letter/{cl.id}/pdf",
        expires_at=cl.expires_at,
    )


async def get_cover_letter_status(
    cl_id: uuid.UUID,
    db: AsyncSession,
    base_url: str,
) -> CoverLetterStatusResponse:
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")

    html_url = None
    pdf_url = None
    letter_data = None
    if cl.status == CoverLetterStatus.ready.value:
        html_url = f"{base_url}/api/cover-letter/{cl_id}/html"
        pdf_url = f"{base_url}/api/cover-letter/{cl_id}/pdf"
        letter_data = cl.letter_data

    return CoverLetterStatusResponse(
        cover_letter_id=cl.id,
        status=cl.status,
        html_url=html_url,
        pdf_url=pdf_url,
        error_message=cl.error_message,
        expires_at=cl.expires_at,
        letter_data=letter_data,
    )


async def get_cover_letter_html(
    cl_id: uuid.UUID,
    db: AsyncSession,
    require_ready: bool = True,
) -> str:
    """Render the cover letter HTML via Jinja2. Only works when status='ready'.

    ``require_ready`` (E037 PQ #2): the generation path renders the smoke PDF before the
    status flips to 'ready' (so the ATS audit lands BEFORE 'ready' is observable). The
    public download path keeps the default ready-only guard.
    """
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    if require_ready and cl.status != CoverLetterStatus.ready.value:
        raise ValueError(f"Cover letter not ready (status={cl.status})")

    color_ctx = _default_color_context()
    if cl.color_profile_id is not None:
        from applire.models.color_profile import ColorProfile
        cp_result = await db.execute(
            select(ColorProfile).where(ColorProfile.id == cl.color_profile_id)
        )
        cp = cp_result.scalar_one_or_none()
        if cp is not None:
            color_ctx = {
                "primary": cp.primary,
                "primary_tint": cp.primary_tint,
                "surface": cp.surface,
                "surface_text": cp.surface_text,
            }

    letter_data = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})
    template_file = _TEMPLATE_FILES.get(cl.template, "lebenslauf_letter.html.j2")
    tmpl = _jinja_env.get_template(template_file)
    # #4 (ADR-038): the subject prefix + html lang follow the document's output language
    # (resolved from the target job, like the CV), not a hardcoded German "Bewerbung".
    from applire.models.job import JobAnalysis
    from applire.templates.labels import cover_letter_labels
    job = await db.get(JobAnalysis, cl.job_analysis_id)
    lang = resolve_jd_language(job) if job else "de"
    # F3 (blind PQ blocker) AC #3: the subject must reference the target role, not
    # just the bare word "Application"/"Bewerbung". Computed at render time (never
    # stored on letter_data) so it always reflects the job's current role_title —
    # role_title lives on JobAnalysis, not in the LLM's letter_data schema.
    labels = cover_letter_labels(lang)
    role_title = job.role_title if job is not None else None
    if role_title:
        subject = f"{labels['subject_prefix']}: {role_title}"
    else:
        subject = labels["subject_prefix"]
    return tmpl.render(
        letter=letter_data,
        color=color_ctx,
        lang=lang,
        labels=labels,
        subject=subject,
    )


async def patch_cover_letter_section(
    cl_id: uuid.UUID,
    section: str,
    content: str,
    db: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")

    overrides = dict(cl.section_overrides or {})
    overrides[section] = content
    cl.section_overrides = overrides
    await db.commit()

    if background_tasks is not None:
        background_tasks.add_task(_update_ats_report_letter_by_id, cl_id)


async def get_cover_letter_by_job(
    job_id: uuid.UUID,
    db: AsyncSession,
    base_url: str,
) -> CoverLetterStatusResponse:
    # Find active cover letter via flow session
    flow_result = await db.execute(
        select(FlowSession).where(
            FlowSession.job_id == job_id,
            FlowSession.deleted_at.is_(None),
        )
    )
    flow = flow_result.scalar_one_or_none()
    if flow is None or flow.generated_cover_letter_id is None:
        raise LookupError(f"No cover letter found for job {job_id}")
    return await get_cover_letter_status(flow.generated_cover_letter_id, db, base_url)


def _default_color_context() -> dict:
    return {
        "primary": "#1a1a2e",
        "primary_tint": "#e8e8f0",
        "surface": "#1a1a2e",
        "surface_text": "#ffffff",
    }


def _inject_letter_date(letter_data: dict, language: str, today: date | None = None) -> dict:
    """Set recipient.date from the system clock, overwriting any LLM value —
    the model cannot know today's date and hallucinates one if asked."""
    letter_data.setdefault("recipient", {})["date"] = format_letter_date(language, today)
    return letter_data


def _apply_recipient_overrides(letter_data: dict, pre_gen_inputs: dict) -> dict:
    """Overlay the user's own dialog input onto letter_data.recipient — F3 (blind PQ
    blocker): the LLM's JSON solely owns letter_data, so a user who typed a recipient
    name/company in the generate dialog could see it silently dropped (LLM returned
    null, or a different value) with no deterministic guarantee it survives. User input
    always wins over the LLM's value when the user provided one (AC #2); when the user
    left a field blank, the LLM's own extracted/guessed value (e.g. from the JD) is kept.
    """
    data = copy.deepcopy(letter_data)
    recipient = data.setdefault("recipient", {})
    for field, key in (("name", "recipient_name"), ("company", "recipient_company")):
        user_value = pre_gen_inputs.get(key)
        if user_value:
            recipient[field] = user_value
    return data


def _apply_section_overrides(letter_data: dict, overrides: dict) -> dict:
    """Return a copy of letter_data with manual section overrides applied."""
    data = copy.deepcopy(letter_data)
    for section, content in overrides.items():
        if section == "body" and isinstance(content, str):
            data.setdefault("body", {})["paragraphs"] = [content]
        elif section in data:
            if isinstance(data[section], dict) and isinstance(content, str):
                data[section]["_override"] = content
            else:
                data[section] = content
    return data


async def _render_cover_letter_background(
    cl_id: uuid.UUID,
    cv_id: uuid.UUID | None,
    job_id: uuid.UUID,
) -> None:
    """Background task: LLM → Jinja2 → PDF. Updates status on completion."""
    async with AsyncSessionLocal() as db:
        try:
            # Load cover letter record
            cl_result = await db.execute(
                select(GeneratedCoverLetter).where(GeneratedCoverLetter.id == cl_id)
            )
            cl = cl_result.scalar_one_or_none()
            if cl is None:
                return

            cl.status = CoverLetterStatus.generating.value
            await db.commit()

            # Load job
            job_result = await db.execute(
                select(JobAnalysis).where(JobAnalysis.id == job_id)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                raise LookupError("Job not found")

            # Load CV tailored_data
            cv_data: dict = {}
            if cv_id is not None:
                cv_result = await db.execute(
                    select(GeneratedCV).where(GeneratedCV.id == cv_id)
                )
                cv = cv_result.scalar_one_or_none()
                if cv is not None:
                    cv_data = cv.tailored_data or {}

            # Load profile
            profile_result = await db.execute(
                select(MasterProfile)
                .where(MasterProfile.deleted_at.is_(None))
                .order_by(MasterProfile.created_at.desc())
                .limit(1)
            )
            profile = profile_result.scalar_one_or_none()
            if profile is not None and not cv_data:
                cv_data = profile.profile_json or {}

            # Auto-extract recipient if not provided
            pre_gen = dict(cl.pre_gen_inputs or {})
            if not pre_gen.get("recipient_name"):
                extracted = extract_recipient_from_jd(job.raw_text)
                if extracted["name"]:
                    pre_gen["recipient_name"] = extracted["name"]
            if not pre_gen.get("recipient_company") and hasattr(job, "company_name") and job.company_name:
                pre_gen["recipient_company"] = job.company_name

            # ADR-038: the letter follows the language the JD is written in —
            # not language_requirement, which is the candidate requirement
            # (e.g. "Bilingual DE/EN") and misroutes.
            detected_language = resolve_jd_language(job)

            # ADR-048 / US201: load the latest Keyword Ledger for this job (read-only,
            # mirrors cv.py) so the prompt surfaces claimable terms with their profile
            # evidence and forbids honest-gap terms. Legacy pre-E037 gap rows have none.
            from applire.models.gap import GapAnalysis
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
            keyword_ledger: list[dict] = (gap.keyword_ledger or []) if gap else []

            # Call LLM
            provider = get_provider()
            user_prompt = build_cover_letter_prompt(
                cv_data=cv_data,
                jd_text=job.raw_text,
                pre_gen_inputs=pre_gen,
                detected_language=detected_language,
                keyword_ledger=keyword_ledger,
                role_title=job.role_title,
            )
            # Explicit budget to match CV generation (cv.py): a signed letter must
            # never close its JSON early under budget pressure (F-B, ADR-009 amendment).
            letter_data = await provider.aparse_json(
                user_prompt, system=SYSTEM_PROMPT, max_tokens=CV_GENERATION_MAX_TOKENS
            )

            # ADR-040 §1 / US170 (JF-M-8.1): the letter is signed and sent, so it carries
            # the same two-tier truthfulness contract as the CV. Prevention tier — a grounding
            # reviewer audits the body for invented dates/employers/achievements before the
            # letter is shown. Source of truth = the grounded CV data + profile + the
            # candidate's OWN inputs (so user-stated facts are not false-flagged).
            grounding_source = json.dumps(
                {
                    "cv_data": cv_data,
                    "profile": profile.profile_json if profile is not None else {},
                    "candidate_inputs": {
                        k: pre_gen.get(k)
                        for k in ("motivation", "salary", "availability")
                        if pre_gen.get(k)
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            # ADR-048 / US202: route the Keyword Ledger to the reviewer (absent-claimable
            # reporting + forbidden honest-gap claim flagging). Grounding outranks coverage.
            from applire.services.keyword_ledger import render_ledger_reviewer_block
            ledger_block = render_ledger_reviewer_block(keyword_ledger)
            if ledger_block:
                grounding_source = f"{grounding_source}\n\n{ledger_block}"
            letter_data = await review_and_refine(
                source=grounding_source,
                draft=letter_data,
                generator_prompt_fn=build_retry_prompt,
                generator_system=COVER_LETTER_REFINEMENT_PROMPT,
                reviewer_prompt_fn=build_review_prompt,
                reviewer_system=REVIEW_SYSTEM_PROMPT,
                provider=provider,
                max_retries=LLM_REVIEW_MAX_RETRIES,
                chain_id="cover_letter",
            )

            # F3 (blind PQ blocker): the recipient the user typed in the generate dialog
            # is overlaid deterministically AFTER the LLM/review step — the LLM's JSON
            # solely owns letter_data, so a typed recipient could otherwise be silently
            # dropped (null) or altered. User input always wins (AC #2).
            letter_data = _apply_recipient_overrides(letter_data, pre_gen)

            # The letter date is system-injected AFTER review — the model never sets it
            # (the prior date-hallucination fix, 2026-06-10). recipient.date stays null
            # through generation + review, then is stamped here from the system clock.
            letter_data = _inject_letter_date(letter_data, detected_language)

            # Store the letter body, but keep status 'generating' for now. E037 PQ #2
            # (ATS "not available" race): the ATS audit must be persisted BEFORE status
            # flips to 'ready', because the frontend fetches the report once with no retry
            # — a 'ready' row that has no report yet read NULL and showed "unavailable"
            # permanently. So: persist letter_data → render smoke PDF → audit (commits the
            # report while still 'generating') → ONLY THEN flip to 'ready'. The ready flip
            # is the last write, so 'ready' is never observable before the report exists.
            cl.letter_data = letter_data
            await db.commit()

            # Generate PDF via Playwright. allow_unready=True lets the renderer work while
            # the letter is still 'generating' (the audit needs the PDF before the flip).
            pdf_bytes: bytes | None = None
            try:
                from applire.services.cover_letter_pdf import render_pdf
                pdf_bytes = await render_pdf(cl_id, allow_unready=True)
            except Exception as pdf_err:
                logger.warning("PDF render failed for CL %s: %s", cl_id, pdf_err)
                # HTML preview still works; PDF download will fail gracefully

            # ADR-039 — persist the ATS audit (commits while status is still 'generating').
            # An audit failure is non-fatal: it leaves ats_report NULL and we still flip ready.
            await _update_ats_report_letter(cl, db, pdf=pdf_bytes)

            # Now flip to 'ready' — the report is already committed, so the frontend's
            # single fetch always sees it.
            cl.status = CoverLetterStatus.ready.value
            await db.commit()

        except Exception as exc:
            logger.exception("Cover letter generation failed for %s: %s", cl_id, exc)
            async with AsyncSessionLocal() as err_db:
                err_result = await err_db.execute(
                    select(GeneratedCoverLetter).where(GeneratedCoverLetter.id == cl_id)
                )
                err_cl = err_result.scalar_one_or_none()
                if err_cl is not None:
                    err_cl.status = CoverLetterStatus.failed.value
                    err_cl.error_message = str(exc)[:500]
                    await err_db.commit()


# ---------------------------------------------------------------------------
# ADR-039: ATS audit persistence helpers (letter twin of services/cv.py)
# ---------------------------------------------------------------------------


async def _latest_keyword_ledger(db: AsyncSession, job_id: uuid.UUID) -> list[dict] | None:
    """Return the latest non-deleted GapAnalysis Keyword Ledger for *job_id* (ADR-048/US203).

    Mirrors the generation-path gap query; ``None`` for legacy pre-E037 rows (then all
    missing keywords default to honest-gap in the ATS report).
    """
    from applire.models.gap import GapAnalysis

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


async def _update_ats_report_letter(
    cl: GeneratedCoverLetter,
    db: AsyncSession,
    pdf: bytes | None = None,
) -> None:
    """ADR-039 — letter twin of services/cv.py:_update_ats_report.

    Engine errors leave ats_report NULL, never raise — an audit failure must
    NEVER fail or alter generation status.

    Deliberately wipes any previous report on error: ADR-039 forbids a persisted
    report describing a document state it was not computed from (no stale reports).
    A NULL report is always preferable to a report computed from old content.

    Args:
        cl:  The cover letter ORM object (already in *db*'s session).
        db:  The active async session.
        pdf: Pre-rendered PDF bytes from the generation path's smoke render.
             When provided, the render is reused and no second Playwright
             launch is needed.  The BackgroundTasks patch path leaves this
             None, triggering a fresh render inside the try block.
    """
    try:
        from applire.services.ats_audit import audit_cover_letter
        from applire.services.cover_letter_pdf import render_pdf

        pdf = pdf if pdf is not None else await render_pdf(cl.id)
        job = await db.get(JobAnalysis, cl.job_analysis_id)
        letter_data = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})
        # ADR-048 / US203: the latest Keyword Ledger buckets each MISSING keyword as
        # missing-claimable vs missing-honest-gap (legacy rows have none → all honest-gap).
        ledger = await _latest_keyword_ledger(db, cl.job_analysis_id)
        cl.ats_report = audit_cover_letter(
            pdf, letter_data, list(job.keywords or []) if job else [], ledger
        ).model_dump()
    except Exception:
        logger.exception("ATS audit failed for cover letter %s — ats_report left NULL", cl.id)
        cl.ats_report = None
    await db.commit()


async def _update_ats_report_letter_by_id(cl_id: uuid.UUID) -> None:
    """BackgroundTasks entrypoint — own session (request session gone by run time)."""
    async with AsyncSessionLocal() as db:
        cl = await db.get(GeneratedCoverLetter, cl_id)
        if cl is not None:
            await _update_ats_report_letter(cl, db)


async def get_cover_letter_ats_report(cl_id: uuid.UUID, db: AsyncSession) -> "ATSReportResponse":
    """Return the persisted ATS report for a cover letter (ADR-039).

    Raises LookupError if the cover letter is not found (→ 404 in the router).
    """
    from applire.schemas.ats import ATSReport, ATSReportResponse

    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    # E037 PQ #2 hardening: a non-conforming stored report must degrade to report:null,
    # never raise (which would surface as an HTTP 500 the frontend can't recover from).
    report = None
    if cl.ats_report:
        try:
            report = ATSReport.model_validate(cl.ats_report)
        except Exception:
            logger.warning(
                "Stored ATS report for cover letter %s is malformed — returning report=null",
                cl.id,
            )
            report = None
    return ATSReportResponse(document_id=cl.id, status=cl.status, report=report)
