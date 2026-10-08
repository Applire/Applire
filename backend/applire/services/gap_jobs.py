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

"""Async gap-analysis job service (E037 N2 — async gap analysis).

The first gap analysis of a fresh job runs heavy real-LLM work (classification +
clustering) that blocks the gaps screen ~2 min and 504s fragilely. These helpers let the
kick-off return immediately (``create_gap_job``) and run the work in a background task
(``run_gap_job_background``, delegating to the existing ``analyze_gaps`` service — so the
migration-0040 input_fingerprint idempotency is preserved), polled via ``get_gap_job``.
Mirrors the async CV-import lifecycle (services/profile/import_jobs.py).
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.redaction import scrub_secrets
from applire.db.session import AsyncSessionLocal
from applire.exceptions import (
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from applire import ownership
from applire.models.gap_job import GapAnalysisJob, GapJobStatus
from applire.providers import get_provider
from applire.services.owner_resolution import resolve_user_id

logger = logging.getLogger(__name__)

# Job states a new kick-off may reuse rather than spawning a duplicate LLM run.
_NON_TERMINAL = (GapJobStatus.pending.value, GapJobStatus.processing.value)


def classify_gap_error(exc: BaseException, *, user_id: uuid.UUID | None = None) -> str:
    """Map a gap-analysis failure to a STABLE machine code (parity with
    classify_import_error). Raw exception text stays internal; the API surfaces only this
    code, localized by the frontend."""
    if isinstance(exc, LLMTruncatedError):
        return "llm_truncated"
    if isinstance(exc, LLMTimeoutError):
        return "llm_timeout"
    if isinstance(exc, LLMRateLimitError):
        return "rate_limited"
    if isinstance(exc, LookupError):
        # analyze_gaps raises LookupError when no job/profile exists yet.
        return "gap_not_found"
    return "gap_failed"


async def _find_nonterminal_job(
    db: AsyncSession, job_analysis_id: uuid.UUID, user_id: uuid.UUID
) -> GapAnalysisJob | None:
    """The caller's newest live (pending/processing) job for a posting, if any.

    Keyed on (user, posting) like ``uq_gap_jobs_live_kickoff`` (ADR-092 cl. 3,
    SF-OWN.7): the posting is shared, another user's kick-off is never reused.
    """
    existing = await db.execute(
        select(GapAnalysisJob)
        .where(
            GapAnalysisJob.user_id == user_id,
            GapAnalysisJob.job_analysis_id == job_analysis_id,
            GapAnalysisJob.status.in_(_NON_TERMINAL),
            GapAnalysisJob.deleted_at.is_(None),
        )
        .order_by(GapAnalysisJob.created_at.desc())
        .limit(1)
    )
    return existing.scalar_one_or_none()


async def create_gap_job(
    db: AsyncSession, *, job_analysis_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> GapAnalysisJob:
    """Create a pending gap-analysis job and return it (the kick-off's immediate handle).

    Concurrent dedup: if a non-terminal job already exists for this job_analysis_id (e.g.
    the overlay and the gaps page both fired), reuse it instead of spawning a second LLM
    run. The SELECT alone lost a 7 ms race (two kickoffs → two full LLM analyses;
    Spaghettieis UAT 2026-07-13), so the uq_gap_jobs_live_kickoff partial unique index
    is the real arbiter — a lost race lands here as IntegrityError and we return the
    winner's job."""
    user_id = resolve_user_id(user_id, "gap_jobs.create_gap_job")
    reused = await _find_nonterminal_job(db, job_analysis_id, user_id)
    if reused is not None:
        return reused

    job = GapAnalysisJob(
        job_analysis_id=job_analysis_id,
        user_id=user_id,
        status=GapJobStatus.pending.value,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await _find_nonterminal_job(db, job_analysis_id, user_id)
        if winner is not None:
            return winner
        raise
    await db.refresh(job)
    return job


async def get_gap_job(
    db: AsyncSession, gap_job_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> GapAnalysisJob | None:
    """Fetch a gap-analysis job, scoped to its owner (IDOR guard). Returns None for an
    unknown/deleted job or a job owned by a different user (S-10). The ownerless
    widening (``job.user_id is None`` readable by everyone) is gone: 0074 made
    ``user_id`` NOT NULL (ADR-092 cl. 4)."""
    user_id = resolve_user_id(user_id, "gap_jobs.get_gap_job")
    job = (
        await db.execute(
            select(GapAnalysisJob).where(
                GapAnalysisJob.id == gap_job_id, GapAnalysisJob.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if job is None or job.deleted_at is not None:
        return None
    return job


async def run_gap_job_background(
    gap_job_id: uuid.UUID,
    job_analysis_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    *,
    session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
) -> None:
    """Run the heavy gap analysis in the background and record the outcome on the job.

    Opens its own DB session (the request session is gone), marks the job ``processing``,
    delegates to the existing ``analyze_gaps`` service (which reuses a fingerprint-matching
    gap_analyses row and skips the LLM), then points ``result_gap_analysis_id`` at the row
    and marks ``ready`` — or, on any failure, marks ``failed`` with a stable error_code and
    keeps the raw text internal. Never raises (a background task has no caller to catch)."""
    # Imported here to avoid a potential import cycle with services.gap.
    from applire.services.gap import analyze_gaps

    # ADR-092 cl. 14: a background task names its owner itself — the request's
    # context is gone by the time this runs in a fresh session.
    user_id = resolve_user_id(user_id, "gap_jobs.run_gap_job_background")
    with ownership.owner_context(user_id):
        await _run_gap_job(gap_job_id, job_analysis_id, user_id, session_factory, analyze_gaps)


async def _run_gap_job(gap_job_id, job_analysis_id, user_id, session_factory, analyze_gaps) -> None:  # noqa: ANN001
    async with session_factory() as db:
        job = await db.get(GapAnalysisJob, gap_job_id)
        if job is None or job.user_id != user_id:
            logger.warning("run_gap_job_background: job %s vanished", gap_job_id)
            return

        job.status = GapJobStatus.processing.value
        await db.commit()

        try:
            result = await analyze_gaps(job_analysis_id, db, get_provider(), user_id=user_id)
        except Exception as exc:  # noqa: BLE001 — background task is the last line of defence
            # Discard any failed transaction so the job-status write below succeeds, then
            # re-fetch the job (it may be expired after rollback).
            await db.rollback()
            code = classify_gap_error(exc)
            logger.warning(
                "run_gap_job_background: gap job %s failed (%s)", gap_job_id, code
            )
            job = await db.get(GapAnalysisJob, gap_job_id)
            if job is not None:
                job.status = GapJobStatus.failed.value
                job.error_code = code
                job.error_message = scrub_secrets(str(exc))[:500]
                await db.commit()
            return

        job.result_gap_analysis_id = result.id
        job.status = GapJobStatus.ready.value
        job.error_code = None
        await db.commit()
