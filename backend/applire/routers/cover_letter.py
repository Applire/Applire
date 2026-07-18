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

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.ats import ATSReportResponse
from applire.schemas.cover_letter import (
    CoverLetterGenerateRequest,
    CoverLetterGenerateResponse,
    CoverLetterStatusResponse,
    SectionOverridePatch,
    SectionOverridePatchResponse,
)
from applire.schemas.oracle import TruthfulnessReportResponse
from applire.services.cover_letter import (
    generate_cover_letter,
    get_cover_letter_ats_report,
    get_cover_letter_by_job,
    get_cover_letter_html,
    get_cover_letter_status,
    get_cover_letter_truthfulness_report,
    patch_cover_letter_section,
)

router = APIRouter(prefix="/api/cover-letter", tags=["cover-letter"])


def _get_provider() -> LLMProvider:
    return get_provider()


@router.post(
    "/generate",
    response_model=CoverLetterGenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_generate(
    body: CoverLetterGenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CoverLetterGenerateResponse:
    """Enqueue async cover letter generation. Returns immediately with status='pending'."""
    base_url = str(request.base_url).rstrip("/")
    try:
        return await generate_cover_letter(body, db, provider, background_tasks, base_url)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/by-job/{job_id}", response_model=CoverLetterStatusResponse)
async def get_by_job(
    job_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CoverLetterStatusResponse:
    base_url = str(request.base_url).rstrip("/")
    try:
        return await get_cover_letter_by_job(job_id, db, base_url)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cl_id}/status", response_model=CoverLetterStatusResponse)
async def get_cl_status(
    cl_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> CoverLetterStatusResponse:
    base_url = str(request.base_url).rstrip("/")
    try:
        return await get_cover_letter_status(cl_id, db, base_url)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cl_id}/ats-report", response_model=ATSReportResponse)
async def get_cl_ats_report(
    cl_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> ATSReportResponse:
    """ADR-039: persisted ATS audit report. `report` is null until generation + audit complete."""
    try:
        return await get_cover_letter_ats_report(cl_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cl_id}/truthfulness-report", response_model=TruthfulnessReportResponse)
async def get_cl_truthfulness_report(
    cl_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> TruthfulnessReportResponse:
    """ADR-052 / US246: persisted truthfulness self-audit. `report` is null until
    generation + self-audit complete (or for pre-Tiramisu rows)."""
    try:
        return await get_cover_letter_truthfulness_report(cl_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{cl_id}/html", response_class=HTMLResponse)
async def get_html(
    cl_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> HTMLResponse:
    try:
        html = await get_cover_letter_html(cl_id, db)
        return HTMLResponse(
            content=html,
            headers={
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": "frame-ancestors 'self'",
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/{cl_id}/pdf")
async def get_pdf(
    cl_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> Response:
    try:
        from applire.services.cover_letter import get_cover_letter_pdf_filename
        from applire.services.cover_letter_pdf import render_pdf
        pdf_bytes = await render_pdf(cl_id)
        filename = await get_cover_letter_pdf_filename(cl_id, db)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.patch("/{cl_id}/section", response_model=SectionOverridePatchResponse)
async def patch_section(
    cl_id: uuid.UUID,
    body: SectionOverridePatch,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SectionOverridePatchResponse:
    try:
        await patch_cover_letter_section(cl_id, body.section, body.content, db, background_tasks)
        return SectionOverridePatchResponse(cover_letter_id=cl_id, section=body.section)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
