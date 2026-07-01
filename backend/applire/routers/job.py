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
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.exceptions import LLMRateLimitError, LLMTimeoutError
from applire.models.gap_job import GapJobStatus
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.gap import (
    GapAnalysisResponse,
    GapJobResponse,
    GapJobStatusResponse,
)
from applire.schemas.job import JobAnalyzeRequest, JobAnalysisResponse
from applire.services.gap import analyze_gaps
from applire.services.gap_jobs import create_gap_job, get_gap_job, run_gap_job_background
from applire.services.job import analyze_jd
from applire.services.scraper import ScraperError, scrape_job_url

router = APIRouter(prefix="/api/job", tags=["job"])


def _get_provider() -> LLMProvider:
    return get_provider()


@router.post("/analyze", response_model=JobAnalysisResponse, status_code=status.HTTP_200_OK)
async def analyze_job_description(
    body: JobAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> JobAnalysisResponse:
    if body.url:
        try:
            text = await scrape_job_url(body.url)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error_code": "jd_url_invalid", "message": str(exc)},
            )
        except ScraperError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error_code": "jd_fetch_failed", "message": exc.reason},
            )
        source_url = body.url
    else:
        text = body.text.strip()  # type: ignore[union-attr]
        source_url = None

    try:
        return await analyze_jd(text, db, provider, source_url=source_url)
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except ValueError as exc:
        # Not-a-JD / unprocessable input (US159 / FMEA 4.5) — a user-input problem,
        # so surface a 422, not a 500. (JSONDecodeError, a ValueError subclass, is
        # handled above and stays a 502.)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{job_id}", response_model=JobAnalysisResponse, status_code=status.HTTP_200_OK)
async def get_job_analysis(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> JobAnalysisResponse:
    """Retrieve a stored JobAnalysis without re-triggering LLM (17.11)."""
    from sqlalchemy import select
    from applire.models.job import JobAnalysis

    result = await db.execute(
        select(JobAnalysis).where(
            JobAnalysis.id == job_id,
            JobAnalysis.deleted_at.is_(None),
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return JobAnalysisResponse.model_validate(job)


@router.post(
    "/{job_id}/gaps/refresh",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh_gap_analysis(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> GapAnalysisResponse:
    """Re-run gap analysis against the current profile (19.11).

    Reflects any profile enrichment from interview answers. Idempotent: if the
    profile is unchanged it returns the existing analysis (no LLM re-run, no score
    wobble — E037 PQ #3). When inputs DID change, the headline score is clamped
    monotonically up so added evidence never lowers it. Required for the animated
    score update in Gap-Click mode.
    """
    try:
        return await analyze_gaps(job_id, db, provider, clamp_to_previous=True)
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/{job_id}/gaps",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def get_latest_gap_analysis(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> GapAnalysisResponse:
    """Return the most recent stored gap analysis for a job — no LLM call."""
    from sqlalchemy import select, desc
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job_result = await db.execute(
        select(JobAnalysis).where(
            JobAnalysis.id == job_id,
            JobAnalysis.deleted_at.is_(None),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    gap_result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(desc(GapAnalysis.created_at))
        .limit(1)
    )
    gap = gap_result.scalar_one_or_none()
    if gap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No gap analysis found for job {job_id}",
        )
    return GapAnalysisResponse.model_validate(gap)


@router.post(
    "/{job_id}/gap-jobs",
    response_model=GapJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_gap_analysis_endpoint(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> GapJobResponse:
    """Start an async gap analysis and return a handle immediately (202).

    The heavy classification + clustering LLM work runs in a background task, so the gaps
    screen can no longer block ~2 min or 504 fragilely. Poll
    GET /api/job/{job_id}/gap-jobs/{gap_job_id} until status is ``ready`` or ``failed``.
    Idempotency (migration 0040 input_fingerprint) is preserved: the background task calls
    the same analyze_gaps, which reuses a matching gap_analyses row and skips the LLM.
    """
    user = await auth.get_current_user(request)
    job = await create_gap_job(db, job_analysis_id=job_id, user_id=user.id)
    background_tasks.add_task(run_gap_job_background, job.id, job_id, user.id)
    return GapJobResponse(gap_job_id=job.id, status=GapJobStatus(job.status))


@router.get(
    "/{job_id}/gap-jobs/{gap_job_id}",
    response_model=GapJobStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_gap_job_status_endpoint(
    job_id: uuid.UUID,
    gap_job_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> GapJobStatusResponse:
    """Poll an async gap analysis. 404 if unknown or owned by another user (IDOR guard)."""
    from applire.models.gap import GapAnalysis

    user = await auth.get_current_user(request)
    job = await get_gap_job(db, gap_job_id, user_id=user.id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gap job not found"
        )
    result = None
    if job.status == GapJobStatus.ready.value and job.result_gap_analysis_id is not None:
        gap = await db.get(GapAnalysis, job.result_gap_analysis_id)
        if gap is not None:
            result = GapAnalysisResponse.model_validate(gap)
    return GapJobStatusResponse(
        gap_job_id=job.id,
        status=GapJobStatus(job.status),
        error_code=job.error_code,
        result=result,
    )
