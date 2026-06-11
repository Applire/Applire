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
import uuid
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
from applire.schemas.cv import CVGenerateResponse, CVStatusResponse, CVTemplate, TailoredCVData
from applire.services.reviewer import review_and_refine
from applire.utils.language_detection import resolve_jd_language
from applire.services.profile.merge import _sort_work_by_date
from applire.constants import LLM_REVIEW_MAX_RETRIES

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert a role title to a URL-safe slug for use in filenames."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


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
) -> CVGenerateResponse:
    """Create a GeneratedCV record and render it.

    REST passes a ``BackgroundTasks`` so rendering runs after the response is
    sent. The MCP/agent channel has no request lifecycle, so it omits it
    (``background_tasks=None``) and we render inline before returning — the agent
    polls ``get_cv_status`` and sees a terminal status on the first read.
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

    # Create pending record
    record = GeneratedCV(
        job_analysis_id=job_id,
        profile_id=profile.id,
        tailored_data={},  # populated by background task
        template=template,
        status=CVGenerationStatus.pending.value,
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
        error_message=record.error_message or ("Generation timed out" if status == CVGenerationStatus.failed and not record.error_message else None),
        expires_at=record.expires_at,
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
            error_message=r.error_message,
            expires_at=r.expires_at,
        )
        for r in records
    ]


# ---------------------------------------------------------------------------
# PDF filename helper
# ---------------------------------------------------------------------------


async def get_pdf_filename(cv_id: uuid.UUID, db: AsyncSession) -> str:
    """Build the Content-Disposition filename for a CV PDF.

    Format: lebenslauf-{role_title_slug}-{cv_id[:8]}.pdf
    Falls back to lebenslauf-cv-{cv_id[:8]}.pdf if job not found.
    """
    record = await _load_cv_ready(cv_id, db)
    job = await db.get(JobAnalysis, record.job_analysis_id)
    role_slug = _slugify(job.role_title) if job and job.role_title else "cv"
    return f"lebenslauf-{role_slug}-{str(cv_id)[:8]}.pdf"


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
    return template.render(cv=tailored, color=color_ctx)


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

            provider: LLMProvider = get_provider()
            tailored_raw: dict = await provider.aparse_json(
                build_user_prompt(
                    job_dict,
                    profile_json,
                    keyword_gaps,
                    critical_gaps,
                    output_language=resolve_jd_language(job),
                ),
                system=SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=8192,
            )

            source_material = _json.dumps(profile_json, ensure_ascii=False, indent=2)

            tailored_raw = await review_and_refine(
                source=source_material,
                draft=tailored_raw,
                generator_prompt_fn=_build_cv_retry_prompt,
                generator_system=CV_TAILORING_REFINEMENT_PROMPT,
                reviewer_prompt_fn=_build_cv_review_prompt,
                reviewer_system=_CV_REVIEW_SYSTEM_PROMPT,
                provider=provider,
                max_retries=LLM_REVIEW_MAX_RETRIES,
                generator_max_tokens=8192,
                chain_id="cv_tailoring",
            )

            tailored = TailoredCVData.model_validate(tailored_raw)

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
            record.status = CVGenerationStatus.ready.value
            record.error_message = None
            await db.commit()

            await _update_ats_report(record, db)   # ADR-039

        except Exception as exc:
            logger.exception("CV generation failed for %s: %s", cv_id, exc)
            try:
                record.status = CVGenerationStatus.failed.value
                record.error_message = str(exc)[:1000]
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


async def _update_ats_report(record: GeneratedCV, db: AsyncSession) -> None:
    """ADR-039: render → extract → audit → persist.

    Engine errors leave ats_report NULL, never raise — an audit failure must
    NEVER fail or alter generation status.

    Deliberately wipes any previous report on error: ADR-039 forbids a persisted
    report describing a document state it was not computed from (no stale reports).
    A NULL report is always preferable to a report computed from old content.
    """
    try:
        from applire.services.ats_audit import audit_cv
        from applire.services.cv_section_editor import apply_overrides_to_tailored

        html = await get_cv_html(record.id, db)
        pdf = await _html_to_pdf(html)
        job = await db.get(JobAnalysis, record.job_analysis_id)
        tailored = TailoredCVData.model_validate(record.tailored_data)
        tailored = apply_overrides_to_tailored(
            tailored, record.content_snapshot, record.section_overrides
        )
        record.ats_report = audit_cv(
            pdf, tailored, list(job.keywords or []) if job else []
        ).model_dump()
    except Exception:
        logger.exception("ATS audit failed for CV %s — ats_report left NULL", record.id)
        record.ats_report = None
    await db.commit()


async def _update_ats_report_by_id(cv_id: uuid.UUID) -> None:
    """BackgroundTasks entrypoint — opens its own session (the request session is gone by run time)."""
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
    report = ATSReport.model_validate(record.ats_report) if record.ats_report else None
    return ATSReportResponse(document_id=record.id, status=record.status, report=report)
