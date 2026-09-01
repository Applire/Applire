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

import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.config import settings
from applire.db.session import get_db
from applire.exceptions import LLMRateLimitError, LLMTimeoutError
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.ats import ATSReportResponse
from applire.schemas.oracle import TruthfulnessReportResponse
from applire.schemas.outcome_critic import OutcomeCriticReportResponse
from applire.schemas.cv import CVGenerateRequest, CVGenerateResponse, CVProfileDiffResponse, CVStatusResponse
from applire.schemas.cv_sections import (
    AssistAnswerRequest,
    AssistAnswerResponse,
    AssistStartRequest,
    AssistStartResponse,
    CVSectionsResponse,
    RewriteRequest,
    RewriteResponse,
    SectionPatchRequest,
    SectionPatchResponse,
)
from applire.services.cv import generate_cv, get_cv_ats_report, get_cv_critic_report, get_cv_docx, get_cv_html, get_cv_pdf, get_cv_status, get_cv_truthfulness_report, get_docx_filename, get_pdf_filename, list_cvs_for_job
from applire.services.cv_diff import get_cv_profile_diff
from applire.services.cv_assist import rewrite_section, start_assist_session, submit_assist_answer
from applire.services.cv_section_editor import get_cv_sections, patch_cv_section

router = APIRouter(prefix="/api/cv", tags=["cv"])


def _get_provider() -> LLMProvider:
    return get_provider()


@router.post(
    "/generate",
    response_model=CVGenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_generate(
    body: CVGenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CVGenerateResponse:
    """Enqueue async CV generation. Returns immediately with status='pending'.
    Poll GET /api/cv/{cv_id}/status until status='ready'."""
    # #232: derive from the operator-configured external origin, not the
    # incoming request's Host — a reverse proxy on a non-80/443 port drops
    # the port from request.base_url, pointing agents/UIs at the wrong origin.
    base_url = settings.applire_base_url.rstrip("/")
    try:
        return await generate_cv(
            body.job_id,
            db,
            provider,
            background_tasks,
            body.template,
            base_url,
            target_pages=body.target_pages,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{cv_id}/status", response_model=CVStatusResponse)
async def get_status(
    cv_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CVStatusResponse:
    """Poll CV generation progress (17.12). Returns pdf_url/html_url only when ready."""
    # #232: derive from the operator-configured external origin, not the
    # incoming request's Host — a reverse proxy on a non-80/443 port drops
    # the port from request.base_url, pointing agents/UIs at the wrong origin.
    base_url = settings.applire_base_url.rstrip("/")
    try:
        return await get_cv_status(cv_id, db, base_url)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{cv_id}/ats-report", response_model=ATSReportResponse)
async def get_cv_ats_report_handler(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> ATSReportResponse:
    """ADR-039: persisted ATS audit report. `report` is null until generation + audit complete."""
    try:
        return await get_cv_ats_report(cv_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cv_id}/truthfulness-report", response_model=TruthfulnessReportResponse)
async def get_cv_truthfulness_report_handler(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> TruthfulnessReportResponse:
    """ADR-052 / US246: persisted truthfulness self-audit. `report` is null until
    generation + self-audit complete (or for pre-Tiramisu rows)."""
    try:
        return await get_cv_truthfulness_report(cv_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cv_id}/critic-report", response_model=OutcomeCriticReportResponse)
async def get_cv_critic_report_handler(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> OutcomeCriticReportResponse:
    """ADR-060 (third amendment) / E049 49.6: the outcome critic's Pass A
    verdict on the ASSEMBLED CV — advisory-only, never gates delivery.
    `report` is null until generation + the critic pass complete (or for
    pre-two-mount rows); `report.reason` distinguishes did-not-run from
    ran-and-found-nothing."""
    try:
        return await get_cv_critic_report(cv_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cv_id}/profile-diff", response_model=CVProfileDiffResponse)
async def get_cv_profile_diff_handler(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CVProfileDiffResponse:
    """US147 / ADR-040: deterministic diff of the generated CV vs the Master Profile,
    for the pre-download review. No LLM; reads persisted artifacts only (retention-safe)."""
    try:
        return await get_cv_profile_diff(cv_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cv_id}/html", response_class=HTMLResponse)
async def get_html(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> HTMLResponse:
    try:
        html = await get_cv_html(cv_id, db)
        return HTMLResponse(
            content=html,
            headers={
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": "frame-ancestors 'self'",
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{cv_id}/pdf")
async def get_pdf(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> Response:
    try:
        pdf_bytes = await get_cv_pdf(cv_id, db)
        filename = await get_pdf_filename(cv_id, db)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{cv_id}/docx")
async def get_docx(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> Response:
    """ADR-079 / E057 / US296: the editable Word export — direct python-docx,
    rendered on demand from tailored_data, no bytes persisted. Mirrors
    GET /{cv_id}/pdf's contract exactly; only the artefact differs."""
    try:
        docx_bytes = await get_cv_docx(cv_id, db)
        filename = await get_docx_filename(cv_id, db)
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=list[CVStatusResponse])
async def get_cvs_for_job(
    job_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> list[CVStatusResponse]:
    """List all CVs for a given job (20.11)."""
    # #232: derive from the operator-configured external origin, not the
    # incoming request's Host — a reverse proxy on a non-80/443 port drops
    # the port from request.base_url, pointing agents/UIs at the wrong origin.
    base_url = settings.applire_base_url.rstrip("/")
    try:
        return await list_cvs_for_job(job_id, db, base_url)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{cv_id}/sections", response_model=CVSectionsResponse)
async def get_sections(
    cv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CVSectionsResponse:
    """Return structured sections with gap hints (23.3). Empty sections if no snapshot yet."""
    try:
        return await get_cv_sections(cv_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/{cv_id}/sections/{section_id}/assist",
    response_model=AssistStartResponse,
)
async def post_section_assist(
    cv_id: uuid.UUID,
    section_id: str,
    body: AssistStartRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> AssistStartResponse:
    """Start a Kaile micro-session for one gap (24.1).

    Returns a single focused question. 422 if gap_id not found.
    """
    try:
        return await start_assist_session(cv_id, section_id, body.gap_id, provider, db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.patch(
    "/{cv_id}/sections/{section_id}/assist",
    response_model=AssistAnswerResponse,
)
async def patch_section_assist(
    cv_id: uuid.UUID,
    section_id: str,
    body: AssistAnswerRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> AssistAnswerResponse:
    """Submit answer to micro-session, receive suggestion (24.2).

    422 if session_id invalid or expired.
    """
    try:
        return await submit_assist_answer(cv_id, section_id, body.session_id, body.answer, provider, db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/{cv_id}/sections/{section_id}/rewrite",
    response_model=RewriteResponse,
)
async def post_section_rewrite(
    cv_id: uuid.UUID,
    section_id: str,
    body: RewriteRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> RewriteResponse:
    """Single-turn directed rewrite for a CV section (Sprint 22, US089).

    User provides free-text directions and optional gap IDs.
    Returns a suggested rewrite — does NOT save or re-render the CV.
    422 if section_id is unknown.
    """
    try:
        return await rewrite_section(
            cv_id, section_id, body.directions, body.gap_ids, provider, db
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.patch(
    "/{cv_id}/sections/{section_id:path}",
    response_model=SectionPatchResponse,
)
async def patch_section(
    cv_id: uuid.UUID,
    section_id: str,
    body: SectionPatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SectionPatchResponse:
    """Write section override and re-render CV HTML (23.4).

    Returns updated HTML and the full list of applied overrides.
    422 if section_id is unknown or content > 10,000 chars.
    """
    try:
        return await patch_cv_section(
            cv_id, section_id, body.content, body.save_to_profile, db, background_tasks
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
