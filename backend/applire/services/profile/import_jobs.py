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

"""Async CV-import job service (E036 follow-up — async import).

A CV upload runs heavy segmented LLM work (extraction + reconcile + enrichment) that, on
a slow/output-capped model, exceeds the request/proxy timeout → 504 → CV dropped. These
helpers let the upload return immediately (``create_import_job``) and run the work in a
background task (``run_import_job_background``, reusing the same ``upload_cv`` service),
polled via ``get_import_job``. Mirrors the async CV-generation lifecycle (services/cv.py).
"""

import asyncio
import json
import logging
import uuid
import weakref
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire import ownership
from applire.constants import UPLOAD_TTL_DAYS
from applire.redaction import scrub_secrets
from applire.db.session import AsyncSessionLocal
from applire.exceptions import (
    LLMRateLimitError,
    OCRUnavailableError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from applire.models.import_job import CVImportJob, CVImportStatus
from applire.ocr import get_ocr_extractor
from applire.providers import get_provider
from applire.schemas.profile import ConflictSummary, CVUploadResponse
from applire.storage import get_storage
from applire.services.profile.owner import resolve_owner

logger = logging.getLogger(__name__)

# PQ F1: import jobs are now all POSTed up-front by the onboarding flow (so a mid-import
# refresh can't lose queued files). Without serialization, two background jobs for the
# same user would interleave the profile merge (_apply_merge is read-latest → write) and
# one CV's data would be lost. This per-user lock makes job processing mutually exclusive;
# asyncio.Lock wakes waiters FIFO, and the client POSTs files sequentially, so jobs run in
# creation order. In-process only — Community runs a single worker; a multi-worker
# deployment would need a DB-level queue instead. A job waiting on the lock stays
# ``pending`` (truthful status for pollers); on process restart the lock table is empty,
# so orphaned rows can never deadlock new imports.
# The table is keyed by event loop (weakly, so a closed loop's locks are collected):
# an asyncio.Lock is bound to the loop it was created on, and background tasks always
# run on the server's single loop in production — the loop key only matters for tests,
# which spin up a fresh loop per test.
_user_import_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[uuid.UUID | None, asyncio.Lock]]" = (
    weakref.WeakKeyDictionary()
)


def _import_lock_for(user_id: uuid.UUID) -> asyncio.Lock:
    """Per-user lock. There is no shared ``None`` slot any more (ADR-092 cl. 4):
    every job has an owner, and two users' imports never serialise on each other."""
    if user_id is None:
        raise ownership.OwnerContextMissing("an import job runs for a user (ADR-092 cl. 4)")
    loop = asyncio.get_running_loop()
    locks = _user_import_locks.setdefault(loop, {})
    lock = locks.get(user_id)
    if lock is None:
        lock = locks.setdefault(user_id, asyncio.Lock())
    return lock


def classify_import_error(exc: BaseException) -> str:
    """Map an import failure to a STABLE machine code (parity with
    cv.classify_generation_error / ADR-047 §4 honest-failure UX). The raw exception text
    stays internal; the API surfaces only this code, localized by the frontend."""
    if isinstance(exc, OCRUnavailableError):
        # #789 L12 — an image/scan on an instance whose OCR backend cannot run.
        return "ocr_unavailable"
    if isinstance(exc, LLMTruncatedError):
        return "llm_truncated"
    if isinstance(exc, LLMTimeoutError):
        return "llm_timeout"
    if isinstance(exc, LLMRateLimitError):
        return "rate_limited"
    # JSONDecodeError and ValidationError are ValueError subclasses — order matters.
    if isinstance(exc, (json.JSONDecodeError, ValidationError, ValueError)):
        return "invalid_document"
    return "import_failed"


#: The only ``source`` hint the import door understands (#789 L31). A ZIP is a LinkedIn
#: export by construction; a PDF is ambiguous (a CV or a LinkedIn "Save to PDF" print),
#: so it takes the LinkedIn adapter only when the caller says so.
LINKEDIN_SOURCE = "linkedin"


def _looks_like_zip(filename: str, content_type: str) -> bool:
    """Same detection as ``routers/profile.py::_is_zip`` (filename or MIME)."""
    return filename.lower().endswith(".zip") or content_type in (
        "application/zip",
        "application/x-zip-compressed",
    )


def _looks_like_pdf(filename: str, content_type: str) -> bool:
    """Same detection as ``routers/profile.py::_is_pdf`` (filename or MIME)."""
    return filename.lower().endswith(".pdf") or content_type == "application/pdf"


def _as_cv_upload_response(result: object) -> CVUploadResponse:
    """Adapt a LinkedIn door's return to the job's one result shape.

    ``import_from_linkedin_zip`` / ``_pdf`` answer a held merge as the GATED
    ``CVUploadResponse`` (passed through) but a merged one as a ``ProfileImportResponse``
    (a profile read model: no ``status``, no ``expires_at``). The poll endpoint serves
    ``CVUploadResponse`` only, so the merged shape is mapped the way ``upload_cv`` maps
    its merge outcome: DRAFT below 0.5 completeness or with open conflicts, else
    COMPLETE; the receipt fields (``merge_status``, ``not_applied``, ``matched``) carry
    over unchanged. ``expires_at`` is the stored source file's retention horizon (the
    ``UploadRecord`` default, ``UPLOAD_TTL_DAYS``).
    """
    if isinstance(result, CVUploadResponse):
        return result
    completeness = (
        result.completeness_score if result.completeness_score is not None else result.completeness
    )
    open_conflicts = [c for c in result.merge_conflicts if not getattr(c, "resolved", False)]
    return CVUploadResponse(
        profile_id=result.id,
        status="DRAFT" if (completeness < 0.5 or bool(open_conflicts)) else "COMPLETE",
        completeness_score=completeness,
        conflicts=[
            ConflictSummary(
                conflict_id=c.conflict_id, section=c.section, field=c.field, source=c.source
            )
            for c in open_conflicts
        ],
        expires_at=datetime.now(timezone.utc) + timedelta(days=UPLOAD_TTL_DAYS),
        merge_status=result.merge_status,
        not_applied=result.not_applied,
        not_applied_loss_count=result.not_applied_loss_count,
        matched=result.matched,
    )


async def create_import_job(
    db: AsyncSession, *, filename: str, user_id: uuid.UUID | None
) -> CVImportJob:
    """Create a pending import job and return it (the upload's immediate handle)."""
    job = CVImportJob(
        filename=(filename or "upload")[:512],
        user_id=resolve_owner(user_id),
        status=CVImportStatus.pending.value,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_import_job(
    db: AsyncSession, import_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> CVImportJob | None:
    """Fetch an import job, scoped to its owner (IDOR guard). Returns None for an
    unknown/deleted job or a job owned by a different user."""
    job = (
        await db.execute(
            select(CVImportJob).where(
                CVImportJob.id == import_id, CVImportJob.user_id == resolve_owner(user_id)
            )
        )
    ).scalar_one_or_none()
    if job is None or job.deleted_at is not None:
        return None
    return job


#: Statuses that mean "this import is still running" (queued or being processed).
ACTIVE_IMPORT_STATUSES = (CVImportStatus.pending.value, CVImportStatus.processing.value)


async def list_import_jobs(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    active: bool = True,
    limit: int = 50,
) -> list[CVImportJob]:
    """List import jobs for a user, oldest first (creation = processing order).

    With ``active=True`` (the dashboard's "import still in progress" indicator, PQ F1)
    only pending/processing jobs are returned, and jobs past their TTL are excluded so
    an orphaned job (e.g. a server restart mid-import) can't pin the indicator forever.

    Owner-keyed like ``get_import_job`` (S-10): a user sees their own jobs only —
    the old "plus userless ones" arm is gone (ADR-092 cl. 4, 0074 NOT NULL).
    """
    stmt = select(CVImportJob).where(
        CVImportJob.deleted_at.is_(None), CVImportJob.user_id == resolve_owner(user_id)
    )
    if active:
        stmt = stmt.where(
            CVImportJob.status.in_(ACTIVE_IMPORT_STATUSES),
            CVImportJob.expires_at > datetime.now(timezone.utc),
        )
    stmt = stmt.order_by(CVImportJob.created_at.asc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def run_import_job_background(
    import_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    job_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    source: str | None = None,
    *,
    session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
) -> None:
    """Run the heavy CV import in the background and record the outcome on the job.

    #789 L31 — the LinkedIn export is dispatched here, inside the one job model: a ZIP
    always runs ``import_from_linkedin_zip``; a PDF runs ``import_from_linkedin_pdf``
    only with ``source="linkedin"`` (otherwise it is a CV and runs ``upload_cv``). Same
    per-user lock, same poll endpoint, same ``CVUploadResponse``-shaped result.

    Opens its own DB session (the request session is gone), marks the job ``processing``,
    delegates to the existing ``upload_cv`` service, then stores the CVUploadResponse and
    marks ``ready`` — or, on any failure, marks ``failed`` with a stable error_code and
    keeps the raw text internal. Never raises (a background task has no caller to catch).

    Jobs for the same user are serialized (PQ F1): the per-user lock is taken BEFORE any
    DB session is opened, so a queued job holds no connection while it waits and its
    status stays ``pending`` until it is actually picked up.
    """
    # Imported here to avoid a circular import (services.profile.__init__ imports this module).
    from applire.services import profile as profile_service

    upload_cv = profile_service.upload_cv
    if _looks_like_zip(filename, content_type):
        adapter: Callable | None = profile_service.import_from_linkedin_zip
    elif source == LINKEDIN_SOURCE and _looks_like_pdf(filename, content_type):
        adapter = profile_service.import_from_linkedin_pdf
    else:
        adapter = None

    user_id = resolve_owner(user_id)
    with ownership.owner_context(user_id):
        async with _import_lock_for(user_id):
            await _process_import_job(
                import_id,
                file_bytes,
                filename,
                content_type,
                job_id,
                user_id,
                upload_cv=upload_cv,
                linkedin_adapter=adapter,
                session_factory=session_factory,
            )


async def _process_import_job(
    import_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    job_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    *,
    upload_cv: Callable,
    linkedin_adapter: Callable | None = None,
    session_factory: Callable[[], AsyncSession],
) -> None:
    """The actual import work — runs with the per-user lock held (see caller)."""
    async with session_factory() as db:
        job = await db.get(CVImportJob, import_id)
        if job is None:
            logger.warning("run_import_job_background: job %s vanished", import_id)
            return

        job.status = CVImportStatus.processing.value
        await db.commit()

        try:
            if linkedin_adapter is not None:
                result = _as_cv_upload_response(
                    await linkedin_adapter(
                        file_bytes,
                        db,
                        get_provider(),
                        storage=get_storage(),
                        filename=filename,
                        user_id=user_id,
                    )
                )
            else:
                result = await upload_cv(
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=content_type,
                    db=db,
                    provider=get_provider(),
                    storage=get_storage(),
                    ocr_extractor=get_ocr_extractor(),
                    job_id=job_id,
                    user_id=user_id,
                )
        except Exception as exc:  # noqa: BLE001 — background task is the last line of defence
            # Discard any failed transaction so the job-status write below succeeds, then
            # re-fetch the job (it may be expired after rollback).
            await db.rollback()
            code = classify_import_error(exc)
            logger.warning(
                "run_import_job_background: import %s failed (%s)", import_id, code
            )
            job = await db.get(CVImportJob, import_id)
            if job is not None:
                job.status = CVImportStatus.failed.value
                job.error_code = code
                job.error_message = scrub_secrets(str(exc))[:500]
                await db.commit()
            return

        job.result = result.model_dump(mode="json")
        job.status = CVImportStatus.ready.value
        job.error_code = None
        await db.commit()
