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
from applire.prompts.cover_letter import SYSTEM_PROMPT, build_cover_letter_prompt
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
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
) -> str:
    """Render the cover letter HTML via Jinja2. Only works when status='ready'."""
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    if cl.status != CoverLetterStatus.ready.value:
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
    return tmpl.render(letter=letter_data, color=color_ctx)


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

            # Call LLM
            provider = get_provider()
            user_prompt = build_cover_letter_prompt(
                cv_data=cv_data,
                jd_text=job.raw_text,
                pre_gen_inputs=pre_gen,
                detected_language=detected_language,
            )
            letter_data = await provider.aparse_json(user_prompt, system=SYSTEM_PROMPT)
            letter_data = _inject_letter_date(letter_data, detected_language)

            # Store and mark ready
            cl.letter_data = letter_data
            cl.status = CoverLetterStatus.ready.value
            await db.commit()

            # Generate PDF via Playwright
            try:
                from applire.services.cover_letter_pdf import render_pdf
                await render_pdf(cl_id)
            except Exception as pdf_err:
                logger.warning("PDF render failed for CL %s: %s", cl_id, pdf_err)
                # HTML preview still works; PDF download will fail gracefully

            await _update_ats_report_letter(cl, db)   # ADR-039

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


async def _update_ats_report_letter(cl: GeneratedCoverLetter, db: AsyncSession) -> None:
    """ADR-039 — letter twin of services/cv.py:_update_ats_report.

    Engine errors leave ats_report NULL, never raise — an audit failure must
    NEVER fail or alter generation status.

    Deliberately wipes any previous report on error: ADR-039 forbids a persisted
    report describing a document state it was not computed from (no stale reports).
    A NULL report is always preferable to a report computed from old content.
    """
    try:
        from applire.services.ats_audit import audit_cover_letter
        from applire.services.cover_letter_pdf import render_pdf

        pdf = await render_pdf(cl.id)
        job = await db.get(JobAnalysis, cl.job_analysis_id)
        letter_data = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})
        cl.ats_report = audit_cover_letter(
            pdf, letter_data, list(job.keywords or []) if job else []
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
    report = ATSReport.model_validate(cl.ats_report) if cl.ats_report else None
    return ATSReportResponse(document_id=cl.id, status=cl.status, report=report)
