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
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
import mimetypes

from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.exceptions import LLMRateLimitError, LLMTimeoutError, LLMTruncatedError
from applire.ocr import get_ocr_extractor
from applire.ocr.base import CVImageExtractor
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.models.uploads import UploadRecord
from applire.schemas.profile import (
    ConflictResolutionRequest,
    CVImportJobListItem,
    CVImportJobResponse,
    CVImportStatusResponse,
    CVUploadResponse,
    EnrichmentRecord,
    LinkedInImportRequest,
    MasterProfileResponse,
    ProfileChangesResponse,
    ProfileHealthResponse,
    ProfileImportResponse,
    StagedResolveRequest,
    StagedResolveResponse,
    UndoLastMergeResponse,
    UploadHistoryItem,
)
from applire.schemas.testimony import TestimonyRequest, TestimonyResult
from applire.models.import_job import CVImportStatus
from applire.services.profile.import_jobs import (
    create_import_job,
    get_import_job,
    list_import_jobs,
    run_import_job_background,
)
from applire.services.profile.snapshots import undo_last_merge
from applire.services.profile.commit import StaleEditError, VaultWriteRevertedError
from applire.services.profile import (
    get_enrichment_history,
    get_profile_changes,
    get_profile_health,
    get_profile,
    import_from_linkedin,
    import_from_linkedin_pdf,
    import_from_linkedin_zip,
    import_from_pdf,
    patch_profile_section,
    profile_exists,
    resolve_conflict,
    resolve_staged_extraction,
    StagedExtractionAlreadyResolved,
    StagedExtractionNotFound,
    upload_cv,
)
from applire.storage import get_storage
from applire.storage.base import StorageProvider
from applire.services.photo import delete_photo, get_photo_bytes, upload_photo
from applire.services.profile.reconcile.testimony_bridge import submit_testimony

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _uid(user: "User | None"):
    """The resolved caller's id. ``require_user`` always yields a user (and sets
    the owner context to it); ``None`` only reaches here when a test calls the
    route function directly — the service then takes the owner context
    (ruling 3d-1), which is the same user on every real request."""
    return getattr(user, "id", None)

# Clean, user-appropriate message for a reconcile that hit the token budget
# (LLMTruncatedError). The merge could not be completed in full, so we fail this
# file rather than persist a silent half-merge — but we never leak the raw
# provider/internal text (stop_reason, model name, Pydantic detail). The frontend's
# per-file N-of-M error path marks just this CV failed and invites a retry.
_TRUNCATION_USER_MESSAGE = (
    "We couldn't fully merge this CV into your profile this time. "
    "Nothing was changed — please try uploading it again."
)

# Clean, user-appropriate message for an LLM timeout on upload/import (ADR-047 §4).
# Never leak the raw provider text ("timed out after 120s", model/provider name).
_TIMEOUT_USER_MESSAGE = (
    "This took longer than expected and didn't finish. "
    "Nothing was changed — please try uploading it again."
)


def _get_provider() -> LLMProvider:
    return get_provider()


def _get_storage() -> StorageProvider:
    return get_storage()


def _get_ocr() -> CVImageExtractor:
    return get_ocr_extractor()


def _is_zip(file: UploadFile) -> bool:
    if file.filename and file.filename.lower().endswith(".zip"):
        return True
    if file.content_type in ("application/zip", "application/x-zip-compressed"):
        return True
    return False


def _is_pdf(file: UploadFile) -> bool:
    if file.filename and file.filename.lower().endswith(".pdf"):
        return True
    if file.content_type == "application/pdf":
        return True
    return False


@router.post("/upload", response_model=CVUploadResponse, status_code=status.HTTP_200_OK)
async def upload_cv_endpoint(
    file: UploadFile,
    request: Request,
    job_id: uuid.UUID | None = Query(default=None, description="Accepted for API compatibility; no longer changes extraction (M5.1.3)"),
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    storage: StorageProvider = Depends(_get_storage),
    ocr: CVImageExtractor = Depends(_get_ocr),
    current_user: User = Depends(require_user),
) -> CVUploadResponse:
    """Upload a CV in any supported format and merge it into the Master Profile.

    Supported formats: PDF (text + OCR fallback for scanned), DOCX, JPEG/PNG, plain text.
    *job_id* is accepted for API compatibility only; it no longer changes extraction (M5.1.3).

    Returns a CVUploadResponse with completeness score, status (DRAFT/COMPLETE),
    any detected conflicts, and the GDPR expiry date for the stored file.

    **Synchronous — prefer ``POST /api/profile/import-jobs`` for anything large or
    slow.** This request blocks for the whole ingest (extraction, review, enrichment,
    merge — several sequential LLM calls), and a reverse proxy cuts it at its read
    timeout (the shipped nginx config: 300 s) while the ingest keeps running and can
    still write the vault minutes after the client was told it failed (#674, measured
    on a self-hosted instance 2026-09-17). The async door returns a handle at once and
    is what the browser UI uses. Retirement of this door is scheduled separately.
    """
    user = current_user
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"

    try:
        file_bytes = await file.read()
        return await upload_cv(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            db=db,
            provider=provider,
            storage=storage,
            ocr_extractor=ocr,
            job_id=job_id,
            user_id=user.id,
        )
    except HTTPException:
        raise
    except LLMTimeoutError:
        logger.warning("profile import/upload: LLM timed out; failing this file cleanly")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=_TIMEOUT_USER_MESSAGE
        )
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMTruncatedError:
        # A reconcile/extraction that ran out of token budget — surface a clean,
        # retryable signal and NEVER the raw provider text (no half-merge persisted).
        logger.warning("upload_cv: LLM output truncated; failing this file cleanly")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_TRUNCATION_USER_MESSAGE,
        )
    except VaultWriteRevertedError as exc:
        # ADR-063 amended 2026-08-28 (#597) — a defence-in-depth reload gate
        # caught a schema-rejecting profile after the ops were already
        # applied; nothing was persisted (same "fail this file cleanly, no
        # half-merge" guarantee LLMTruncatedError gives above, one layer
        # deeper — the write itself, not the LLM call, is what reverted).
        logger.error("upload_cv: vault write reverted (%s); failing this file cleanly", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "vault_write_reverted", "message": _TRUNCATION_USER_MESSAGE},
        )
    except json.JSONDecodeError:
        # Must come before ValueError — JSONDecodeError is a ValueError subclass
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except ValidationError as exc:
        # Must come before ValueError — ValidationError is a ValueError subclass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="We couldn't read some details from your document (for example an unrecognised date). Please check the file and try again.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.post(
    "/import-jobs",
    response_model=CVImportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_cv_import_endpoint(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    request: Request,
    job_id: uuid.UUID | None = Query(
        default=None, description="Accepted for API compatibility; no longer changes extraction (M5.1.3)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> CVImportJobResponse:
    """Start an async CV import and return a handle immediately (202).

    Mirrors the async CV-generation lifecycle: the heavy segmented extraction + reconcile
    + enrichment runs in a background task, so a slow/output-capped model can't 504 the
    request and drop the CV. Poll GET /api/profile/import-jobs/{import_id} until the status
    is ``ready`` (``result`` holds the CVUploadResponse) or ``failed``. This is the
    recommended door for every REST caller; the sync ``/upload`` remains only for
    compatibility and is cut by a proxy read timeout on a slow route (#674).
    """
    user = current_user
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    file_bytes = await file.read()

    job = await create_import_job(db, filename=filename, user_id=user.id)
    background_tasks.add_task(
        run_import_job_background,
        job.id,
        file_bytes,
        filename,
        content_type,
        job_id,
        user.id,
    )
    return CVImportJobResponse(import_id=job.id, status=CVImportStatus(job.status))


@router.get(
    "/import-jobs",
    response_model=list[CVImportJobListItem],
    status_code=status.HTTP_200_OK,
)
async def list_cv_import_jobs_endpoint(
    request: Request,
    active: bool = Query(
        default=True,
        description="Only jobs still running (pending/processing, not expired)",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> list[CVImportJobListItem]:
    """List the current user's async CV imports, oldest first (PQ F1).

    Default (``active=true``) returns only pending/processing jobs — the lightweight
    signal the dashboard uses to show a truthful "profile import still in progress"
    indicator after a refresh interrupted the onboarding overlay. User-scoped (same
    IDOR guard as GET /import-jobs/{id}); another user's jobs are never listed.
    """
    user = current_user
    jobs = await list_import_jobs(db, user_id=user.id, active=active)
    return [
        CVImportJobListItem(
            import_id=j.id,
            status=CVImportStatus(j.status),
            filename=j.filename,
            created_at=j.created_at,
        )
        for j in jobs
    ]


@router.get(
    "/import-jobs/{import_id}",
    response_model=CVImportStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_cv_import_status_endpoint(
    import_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> CVImportStatusResponse:
    """Poll an async CV import. 404 if unknown or owned by another user (IDOR guard)."""
    user = current_user
    job = await get_import_job(db, import_id, user_id=user.id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    result = (
        CVUploadResponse.model_validate(job.result)
        if job.status == CVImportStatus.ready.value and job.result is not None
        else None
    )
    return CVImportStatusResponse(
        import_id=job.id,
        status=CVImportStatus(job.status),
        error_code=job.error_code,
        result=result,
    )


@router.post(
    "/staged/{staged_id}/resolve",
    response_model=StagedResolveResponse,
    status_code=status.HTTP_200_OK,
)
async def resolve_staged_extraction_endpoint(
    staged_id: uuid.UUID,
    body: StagedResolveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    provider: LLMProvider = Depends(_get_provider),
) -> StagedResolveResponse:
    """Resolve a CV upload that the pre-merge integrity gate held (US167).

    ``action="merge"`` applies the parked extraction additively to the Master
    Profile (re-using the original LLM result — no re-extraction); ``"discard"``
    drops it, leaving the profile untouched. Resolving is idempotent: a second
    attempt on an already-resolved item returns HTTP 409. The lookup is scoped to
    the authenticated user, so a foreign upload returns 404 (IDOR guard).
    """
    user = current_user
    try:
        return await resolve_staged_extraction(
            db, staged_id, action=body.action, user_id=user.id, provider=provider
        )
    except StagedExtractionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except StagedExtractionAlreadyResolved as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post(
    "/undo-last-merge",
    response_model=UndoLastMergeResponse,
    status_code=status.HTTP_200_OK,
)
async def undo_last_merge_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> UndoLastMergeResponse:
    """Undo the last Master Profile merge (US168 / ADR-042).

    Restores the most recent pre-merge snapshot, clearing the conflicts that merge
    introduced. If edits occurred after the merge, the restore still proceeds but
    ``discarded_later_edits`` warns that those changes were dropped (coarse
    whole-profile restore; per-field revert deferred). Idempotent: a repeat call
    with nothing left to undo returns ``restored=false``.
    """
    result = await undo_last_merge(db, user_id=_uid(current_user))
    return UndoLastMergeResponse(
        restored=result.restored,
        discarded_later_edits=result.discarded_later_edits,
    )


@router.post(
    "/import",
    response_model=ProfileImportResponse | CVUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def import_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    storage: StorageProvider = Depends(_get_storage),
    current_user: User = Depends(require_user),
    file: Annotated[UploadFile | None, File(description="LinkedIn export ZIP")] = None,
    linkedin_json: Annotated[str | None, Form(description="LinkedIn export JSON string")] = None,
) -> ProfileImportResponse | CVUploadResponse:
    """Structured data ingestor for LinkedIn/XING exports (ZIP, PDF or JSON).

    For CV file uploads (PDF, DOCX, images), use ``POST /api/profile/import-jobs``
    (async, poll ``GET /api/profile/import-jobs/{id}``) — not the synchronous
    ``/upload``, which a reverse proxy cuts at its read timeout while the ingest keeps
    writing (#674). Note that this door is synchronous too and, since #367, runs the
    same full ingest chain.

    Since #367 (2026-09-13, ruling V-2) this is the third adapter over the one
    ingest function, so the US167/ADR-041 pre-merge integrity gate fires here too:
    an export whose name has no token overlap with the account holder's, or one
    that extracts to nothing, is **held** and answered with the same GATED
    ``CVUploadResponse`` the browser upload door returns — `status="GATED"` plus
    `gate` / `staged_id`, resolved through ``POST /staged/{id}/resolve``.
    """
    if file is None and linkedin_json is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either a LinkedIn export ZIP or linkedin_json",
        )
    if file is not None and linkedin_json is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either a file or linkedin_json, not both",
        )

    user = current_user
    try:
        if file is not None:
            file_bytes = await file.read()
            upload_name = file.filename or "linkedin-export"
            if _is_zip(file):
                coro = import_from_linkedin_zip(
                    file_bytes, db, provider, storage=storage,
                    filename=upload_name, user_id=user.id,
                )
            elif _is_pdf(file):
                coro = import_from_linkedin_pdf(
                    file_bytes, db, provider, storage=storage,
                    filename=upload_name, user_id=user.id,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Only LinkedIn export ZIP or PDF files are accepted here.",
                )
        else:
            try:
                parsed = json.loads(linkedin_json)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="linkedin_json is not valid JSON",
                )
            coro = import_from_linkedin(
                parsed, db, provider, storage=storage, user_id=user.id
            )

        return await coro

    except HTTPException:
        raise
    except LLMTimeoutError:
        logger.warning("profile import/upload: LLM timed out; failing this file cleanly")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=_TIMEOUT_USER_MESSAGE
        )
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMTruncatedError:
        # See upload_cv_endpoint — a truncated reconcile/extraction fails this
        # import cleanly (no half-merge), with no raw provider/internal text.
        logger.warning("import_profile: LLM output truncated; failing this import cleanly")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_TRUNCATION_USER_MESSAGE,
        )
    except VaultWriteRevertedError as exc:
        # See upload_cv_endpoint — same translation, one layer deeper (the
        # write itself reverted, not the LLM call).
        logger.error("import_profile: vault write reverted (%s); failing this import cleanly", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "vault_write_reverted", "message": _TRUNCATION_USER_MESSAGE},
        )
    except json.JSONDecodeError:
        # Must come before ValueError — JSONDecodeError is a ValueError subclass
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except ValidationError as exc:
        # Must come before ValueError — ValidationError is a ValueError subclass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="We couldn't read some details from your document (for example an unrecognised date). Please check the file and try again.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


# ---------------------------------------------------------------------------
# Photo endpoints
# ---------------------------------------------------------------------------


@router.post("/photo", status_code=status.HTTP_200_OK)
async def upload_photo_endpoint(
    file: UploadFile,
    request: Request,
    consent: bool = Query(default=False, description="Must be True — GDPR Art. 9(2)(a) explicit consent"),
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    """Upload a profile photo. Consent must be explicitly provided.

    Accepted formats: JPEG, PNG, WebP. Max 5 MB.
    Photo is stored and photo_url is set in the Master Profile personal_info.
    Re-uploading replaces the existing photo and refreshes consent_at.
    """
    user = current_user
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consent is required to store your photo (GDPR Art. 9).",
        )
    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    try:
        return await upload_photo(
            user_id=user.id,
            file_bytes=file_bytes,
            content_type=content_type,
            db=db,
            storage=storage,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/photo", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    current_user: User = Depends(require_user),
) -> None:
    """Delete the profile photo and clear GDPR consent."""
    user = current_user
    try:
        await delete_photo(user_id=user.id, db=db, storage=storage)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/photo", status_code=status.HTTP_200_OK)
async def get_photo_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    current_user: User = Depends(require_user),
) -> Response:
    """Return the raw photo bytes (GDPR data portability). 404 if no photo on file."""
    user = current_user
    try:
        photo_bytes, media_type = await get_photo_bytes(user_id=user.id, db=db, storage=storage)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile photo on file",
        )
    return Response(content=photo_bytes, media_type=media_type)


@router.get("/exists")
async def check_profile_exists(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """Lightweight check: returns exists + completeness_score (no full profile payload)."""
    return await profile_exists(db, user_id=_uid(current_user))


@router.get("", response_model=MasterProfileResponse, status_code=status.HTTP_200_OK)
async def get_current_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    profile = await get_profile(db, user_id=_uid(current_user))
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile found. Import a CV first.",
        )
    return profile


@router.get("/uploads", response_model=list[UploadHistoryItem], status_code=status.HTTP_200_OK)
async def get_upload_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> list[UploadHistoryItem]:
    """Return the last 10 uploads for the current user, newest first."""
    user = current_user
    result = await db.execute(
        select(UploadRecord)
        .where(UploadRecord.user_id == user.id)
        .order_by(UploadRecord.created_at.desc())
        .limit(10)
    )
    records = result.scalars().all()
    return [
        UploadHistoryItem(
            id=r.id,
            original_filename=r.original_filename,
            mime_type=r.mime_type,
            byte_size=r.byte_size,
            created_at=r.created_at,
            completeness_score=None,  # TODO: link to EnrichmentRecord for actual score
            gate_status=r.gate_status,
            staged_name=(
                (r.staged_extraction or {}).get("personal_info", {}).get("name")
                if r.staged_extraction
                else None
            ),
        )
        for r in records
    ]


@router.get(
    "/enrichment-history",
    response_model=list[EnrichmentRecord],
    status_code=status.HTTP_200_OK,
)
async def get_profile_enrichment_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> list[EnrichmentRecord]:
    return await get_enrichment_history(db, user_id=_uid(current_user))


@router.get(
    "/changes",
    response_model=ProfileChangesResponse,
    status_code=status.HTTP_200_OK,
)
async def get_profile_changes_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ProfileChangesResponse:
    """US145 / ADR-040 — the "what changed & why" surface data: the decision trail
    plus pending conflicts, read from the Master Profile only (retention-independent)."""
    return await get_profile_changes(db, user_id=_uid(current_user))


@router.get(
    "/health",
    response_model=ProfileHealthResponse,
    status_code=status.HTTP_200_OK,
)
async def get_profile_health_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ProfileHealthResponse:
    """US160 (E033 / ADR-041 amended) — deterministic Profile Health: conflict +
    accuracy issues (severity-tagged) plus a completeness block. No LLM; reads
    only the durable Master Profile (never the 7-day upload — ADR-005)."""
    return await get_profile_health(db, user_id=_uid(current_user))


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def resolve_profile_conflict(
    conflict_id: str,
    body: ConflictResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    try:
        return await resolve_conflict(
            conflict_id, body.resolution, body.value, db, user_id=_uid(current_user)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/testimony",
    response_model=TestimonyResult,
    status_code=status.HTTP_200_OK,
)
async def submit_testimony_endpoint(
    body: TestimonyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    provider: LLMProvider = Depends(_get_provider),
) -> TestimonyResult:
    """#258 — the UI door for free-text testimony ("anything else recruiters
    should know"). One reconcile call — the same order of magnitude as a
    single interview turn or `submit_claims` claim, not the multi-call
    segmented CV-import pipeline — so this stays a direct, synchronous
    endpoint rather than an async job (see routers/profile.py's
    /import-jobs for that pattern, reserved for genuinely multi-call work).
    Calls the exact same `submit_testimony` service the MCP `submit_testimony`
    tool calls (ADR-058 door parity)."""
    try:
        return await submit_testimony(body.text, db, provider, user_id=_uid(current_user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.patch("/{section}", response_model=MasterProfileResponse, status_code=status.HTTP_200_OK)
async def patch_section(
    section: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    provider: LLMProvider = Depends(_get_provider),
    basis_updated_at: Annotated[
        datetime | None,
        Query(
            description=(
            "The profile's `updated_at` from the GET this edit was composed "
            "against (ADR-063 amended 2026-08-25). When the profile has moved "
            "since, the write is refused with 409 and the current profile in "
            "`detail.current`. Omit for last-write-wins."
            ),
        ),
    ] = None,
) -> MasterProfileResponse:
    body = await request.json()
    try:
        return await patch_profile_section(
            section, body, db, provider=provider, basis_updated_at=basis_updated_at,
            user_id=_uid(current_user),
        )
    except StaleEditError as exc:
        current = await get_profile(db, user_id=_uid(current_user))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "stale_edit",
                "current_updated_at": exc.current_updated_at.isoformat(),
                "current": current.model_dump(mode="json"),
            },
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.delete("", status_code=status.HTTP_202_ACCEPTED)
async def erase_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    current_user: User = Depends(require_user),
) -> dict:
    """GDPR Art. 17 — erase the CALLER's vault (ADR-092 cl. 11, US336).

    An adapter over the one implementation, ``services.erasure.erase(db,
    user_id, "vault")``: every per-user row of this user, leaf → root and keyed
    on the owner, in one transaction; shared postings nobody else references are
    purged (lock first, check second); files after the commit. Another user's
    rows and any posting another user still references are untouched. The user
    row stays (the account is deleted by ``DELETE /api/me/account``). 202 Accepted.
    """
    from applire.services.erasure import ErasureFailed, erase

    try:
        from applire.services.profile.owner import resolve_owner

        counts = await erase(db, resolve_owner(_uid(current_user)), "vault", storage=storage)
    except ErasureFailed:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erasure failed — no data was deleted. Please retry.",
        )
    return {"message": "Erasure accepted", "records_deleted": counts}


@router.get("/export")
async def export_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> JSONResponse:
    """GDPR Art. 20 — data portability. Returns complete user data as JSON download.

    Excludes internal system state (raw_text_hash, token counts, etc.).
    """
    from applire.models.application import Application
    from applire.models.cv import GeneratedCV
    from applire.models.session import InterviewSession
    from applire.models.uploads import UploadRecord

    user = current_user
    uid = user.id

    # The caller's own live profile (ADR-092 cl. 2) — the one read path.
    from applire.services.profile import get_profile_for_user

    profile = await get_profile_for_user(db, uid)

    # Applications
    apps_result = await db.execute(
        select(Application).where(Application.user_id == uid, Application.deleted_at.is_(None))
    )
    apps = apps_result.scalars().all()

    # Interview sessions (via profile)
    interview_data: list[dict] = []
    if profile:
        sess_result = await db.execute(
            select(InterviewSession).where(
                InterviewSession.user_id == uid,
                InterviewSession.profile_id == profile.id,
                InterviewSession.deleted_at.is_(None),
            )
        )
        for s in sess_result.scalars().all():
            interview_data.append({
                "id": str(s.id),
                "mode": s.mode,
                "status": s.status,
                "questions_asked": s.questions_asked,
                "created_at": s.created_at.isoformat(),
            })

    # Uploads
    uploads_result = await db.execute(
        select(UploadRecord).where(UploadRecord.user_id == uid)
    )
    uploads = [
        {
            "id": str(u.id),
            "original_filename": u.original_filename,
            "mime_type": u.mime_type,
            "byte_size": u.byte_size,
            "created_at": u.created_at.isoformat(),
        }
        for u in uploads_result.scalars().all()
    ]

    # Strip internal system state from profile_json — GDPR Art. 20 covers only
    # "data provided by the data subject", not system-derived metadata.
    # enrichment_history: internal audit trail of what changed and how
    # pending_conflicts: system-detected data inconsistencies, not user data
    profile_export: dict | None = None
    if profile and profile.profile_json:
        profile_export = dict(profile.profile_json)
        if isinstance(profile_export.get("metadata"), dict):
            meta = dict(profile_export["metadata"])
            meta.pop("enrichment_history", None)
            meta.pop("pending_conflicts", None)
            profile_export["metadata"] = meta

    export: dict = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {"id": str(uid), "email": user.email},
        "profile": profile_export,
        "applications": [
            {
                "id": str(a.id),
                "company_name": a.company_name,
                "role_title": a.role_title,
                "user_status": a.user_status,
                "workflow_status": a.workflow_status,
                "notes": a.notes,
                "applied_at": a.applied_at.isoformat() if a.applied_at else None,
                "deadline": a.deadline.isoformat() if a.deadline else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in apps
        ],
        "interview_sessions": interview_data,
        "uploads": uploads,
    }

    return JSONResponse(
        content=export,
        headers={"Content-Disposition": 'attachment; filename="applire-export.json"'},
    )
