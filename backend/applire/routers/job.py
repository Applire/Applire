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
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.internal_errors import internal_server_error
from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.exceptions import LLMRateLimitError, LLMTimeoutError
from applire.models.gap_job import GapJobStatus
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.gap import (
    GapAnalysisResponse,
    GapJobResponse,
    GapJobStatusResponse,
    GapLeftOpenRequest,
    KeywordLiabilityDowngradeRequest,
)
from applire.schemas.job import JobAnalyzeRequest, JobAnalysisResponse
from applire.services.gap import analyze_gaps, downgrade_keyword_liability, set_cluster_left_open
from applire.services.gap_coverage import AnswerScope, LeftOpenRefused
from applire.services.gap_jobs import create_gap_job, get_gap_job, run_gap_job_background
from applire.services.job import analyze_jd, caller_link, get_job_for_user
from applire.services.posting_labels import posting_response
from applire.services.scraper import ScraperError, scrape_job_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/job", tags=["job"])


def _get_provider() -> LLMProvider:
    return get_provider()


async def _linked_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """The posting ``job_id`` through the caller's link (ADR-092 cl. 5c), else 404.

    A dependency so a route can declare it BEFORE the provider: a foreign or
    missing id answers 404 before any provider is even constructed (S-10).
    """
    return await get_job_for_user(db, job_id, current_user.id)


@router.post("/analyze", response_model=JobAnalysisResponse, status_code=status.HTTP_200_OK)
async def analyze_job_description(
    body: JobAnalyzeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    current_user: User = Depends(require_user),
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
                detail={"error_code": exc.code, "message": exc.reason},
            )
        source_url = body.url
        origin = "scraped"
    else:
        text = body.text.strip()  # type: ignore[union-attr]
        source_url = None
        origin = "supplied"

    try:
        # ADR-092 cl. 5 / RD-2: analyze links the posting to the caller (their
        # application row) and returns their labels + the Branch-F repost hint
        # (computed before the link, so the fresh link never flags itself).
        analysis = await analyze_jd(
            text, db, provider, source_url=source_url,
            user_id=current_user.id, raw_text_origin=origin,
        )
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
        raise internal_server_error(exc, where="job.analyze_job_description", logger=logger)
    return analysis


@router.get("/{job_id}", response_model=JobAnalysisResponse, status_code=status.HTTP_200_OK)
async def get_job_analysis(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> JobAnalysisResponse:
    """Retrieve a stored JobAnalysis without re-triggering LLM (17.11).

    Reachable only through the caller's link to the shared posting
    (``get_job_for_user``, ADR-092 cl. 5c) — 404 otherwise (S-10). The labels
    are the caller's own (``effective_posting_labels``, cl. 5f).
    """
    job = await get_job_for_user(db, job_id, current_user.id)
    # The caller's own row — a hidden repost link (soft-deleted, 4a-1) included.
    app = await caller_link(db, job.id, current_user.id)
    # MD-31: labels AND source_url from the caller's own row, never the shared one.
    return posting_response(job, app)


@router.post(
    "/{job_id}/gaps/refresh",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh_gap_analysis(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    _job=Depends(_linked_job),  # before the provider: 404 first
    provider: LLMProvider = Depends(_get_provider),
) -> GapAnalysisResponse:
    """Re-run gap analysis against the current profile (19.11).

    Reflects any profile enrichment from interview answers. Idempotent: if the
    profile is unchanged it returns the existing analysis (no LLM re-run, no score
    wobble — E037 PQ #3). When inputs DID change, the recompute is ANSWER-DRIVEN
    with nothing touched (``AnswerScope()``, ADR-089 clause 5): every requirement
    is merged with its previous row, so no requirement moves down unless it is a
    new denial or its vault backing is gone (the two floors run on the merged
    ledger), and the clusters carry forward with their per-gap record (clause 4).
    The gaps page replaces its whole analysis with this response after the turn
    that completes a micro-session (clause 8).
    """
    try:
        return await analyze_gaps(
            job_id, db, provider, answer_scope=AnswerScope(), user_id=current_user.id
        )
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
        raise internal_server_error(exc, where="job.refresh_gap_analysis", logger=logger)


@router.post(
    "/{job_id}/gaps/liabilities/downgrade",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def downgrade_gap_keyword_liability(
    job_id: uuid.UUID,
    request: KeywordLiabilityDowngradeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> GapAnalysisResponse:
    """#260 exit (b) — the pre-generation liability summary's "drop the
    keyword" action. Deterministic, no LLM: flips the matching claimable
    ledger entry to an honest gap and re-derives the match score. The other
    exit stays the existing POST /api/session (target_gap) micro-session
    flow — this endpoint only ever removes a claim, never adds one.
    """
    await get_job_for_user(db, job_id, current_user.id)
    try:
        return await downgrade_keyword_liability(
            job_id, request.concept, db, user_id=current_user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception:
        # #265: never format str(exc) into the response — a provider crash's
        # message can embed a raw provider payload. Full detail is logged
        # server-side; the client gets a stable machine-readable code (the
        # #256 convention, applied here so new code adds no new leak).
        logger.exception("keyword-liability downgrade failed for job %s", job_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "internal_error",
                "message": "An unexpected error occurred. Please try again.",
            },
        )


@router.post(
    "/{job_id}/gaps/{cluster_id}/left-open",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def set_gap_left_open(
    job_id: uuid.UUID,
    cluster_id: str,
    request: GapLeftOpenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> GapAnalysisResponse:
    """Ruling K-1 (ADR-089 amended 2026-09-27) — "Leave this gap open"
    (``left_open: true``) and "Pick it up again" (``false``) on the gaps page.

    Deterministic, no LLM: records the candidate's own "enough" on the gap's
    per-gap record (``outcome.left_open``) and returns the whole analysis, like
    the read route, so the page adopts it. The gap stays a gap — score,
    members and budget do not move. 409 ``gap_not_askable`` when leaving open
    a gap that is covered, declined or out of questions (nothing is open to
    leave); 404 when the job, its analysis or the gap is unknown.
    """
    from applire.services.gap import stored_analysis_inputs_changed

    job = await get_job_for_user(db, job_id, current_user.id)
    try:
        gap = await set_cluster_left_open(
            job_id, cluster_id, request.left_open, db, user_id=current_user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except LeftOpenRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "gap_not_askable", "message": str(exc)},
        )
    response = GapAnalysisResponse.model_validate(gap)
    response.inputs_changed = await stored_analysis_inputs_changed(
        gap, job, db, user_id=current_user.id
    )
    return response


@router.get(
    "/{job_id}/gaps",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def get_latest_gap_analysis(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> GapAnalysisResponse:
    """Return the most recent stored gap analysis for a job — no LLM call.

    ADR-090 clause 8: this read never re-runs the analysis, also when the
    profile changed since it was computed. It says so instead —
    ``inputs_changed`` compares the row's gap-relevant fingerprint with the
    current profile — and the gap view offers the re-check
    (``POST /gaps/refresh``)."""
    from sqlalchemy import select, desc
    from applire.models.gap import GapAnalysis

    job = await get_job_for_user(db, job_id, current_user.id)

    # ADR-092 cl. 3 / S-17: the posting is shared, the gap analysis is the
    # caller's — newest gap BY OWNER, never another user's analysis of it.
    gap_result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.user_id == current_user.id,
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
    from applire.services.gap import stored_analysis_inputs_changed

    response = GapAnalysisResponse.model_validate(gap)
    response.inputs_changed = await stored_analysis_inputs_changed(
        gap, job, db, user_id=current_user.id
    )
    return response


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
    current_user: User = Depends(require_user),
) -> GapJobResponse:
    """Start an async gap analysis and return a handle immediately (202).

    The heavy classification + clustering LLM work runs in a background task, so the gaps
    screen can no longer block ~2 min or 504 fragilely. Poll
    GET /api/job/{job_id}/gap-jobs/{gap_job_id} until status is ``ready`` or ``failed``.
    Idempotency (migration 0040 input_fingerprint) is preserved: the background task calls
    the same analyze_gaps, which reuses a matching gap_analyses row and skips the LLM.
    """
    user = current_user
    await get_job_for_user(db, job_id, user.id)  # ADR-092 cl. 5c: 404 without a link
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
    current_user: User = Depends(require_user),
) -> GapJobStatusResponse:
    """Poll an async gap analysis. 404 if unknown or owned by another user (IDOR guard)."""
    from applire.models.gap import GapAnalysis

    user = current_user
    job = await get_gap_job(db, gap_job_id, user_id=user.id)
    if job is None or job.job_analysis_id != job_id:  # the path names ONE posting
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Gap job not found"
        )
    result = None
    if job.status == GapJobStatus.ready.value and job.result_gap_analysis_id is not None:
        gap = await db.get(GapAnalysis, job.result_gap_analysis_id)
        if gap is not None and gap.user_id == user.id:
            result = GapAnalysisResponse.model_validate(gap)
    return GapJobStatusResponse(
        gap_job_id=job.id,
        status=GapJobStatus(job.status),
        error_code=job.error_code,
        result=result,
    )
