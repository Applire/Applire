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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
import mimetypes

from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.exceptions import LLMRateLimitError, LLMTimeoutError
from applire.ocr import get_ocr_extractor
from applire.ocr.base import CVImageExtractor
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.models.uploads import UploadRecord
from applire.schemas.profile import (
    ConflictResolutionRequest,
    CVUploadResponse,
    EnrichmentRecord,
    LinkedInImportRequest,
    MasterProfileResponse,
    ProfileChangesResponse,
    ProfileHealthResponse,
    StagedResolveRequest,
    StagedResolveResponse,
    UndoLastMergeResponse,
    UploadHistoryItem,
)
from applire.services.profile.snapshots import undo_last_merge
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

router = APIRouter(prefix="/api/profile", tags=["profile"])


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
    job_id: uuid.UUID | None = Query(default=None, description="Optional JobAnalysis ID for JD-context-aware extraction"),
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    storage: StorageProvider = Depends(_get_storage),
    ocr: CVImageExtractor = Depends(_get_ocr),
    auth: AuthProvider = Depends(get_auth_provider),
) -> CVUploadResponse:
    """Upload a CV in any supported format and merge it into the Master Profile.

    Supported formats: PDF (text + OCR fallback for scanned), DOCX, JPEG/PNG, plain text.
    Provide an optional *job_id* to enable JD-context-aware extraction, which produces
    more accurate relevance scoring for the target role.

    Returns a CVUploadResponse with completeness score, status (DRAFT/COMPLETE),
    any detected conflicts, and the GDPR expiry date for the stored file.
    """
    user = await auth.get_current_user(request)
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
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
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
    "/staged/{staged_id}/resolve",
    response_model=StagedResolveResponse,
    status_code=status.HTTP_200_OK,
)
async def resolve_staged_extraction_endpoint(
    staged_id: uuid.UUID,
    body: StagedResolveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
    provider: LLMProvider = Depends(_get_provider),
) -> StagedResolveResponse:
    """Resolve a CV upload that the pre-merge integrity gate held (US167).

    ``action="merge"`` applies the parked extraction additively to the Master
    Profile (re-using the original LLM result — no re-extraction); ``"discard"``
    drops it, leaving the profile untouched. Resolving is idempotent: a second
    attempt on an already-resolved item returns HTTP 409. The lookup is scoped to
    the authenticated user, so a foreign upload returns 404 (IDOR guard).
    """
    user = await auth.get_current_user(request)
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
    auth: AuthProvider = Depends(get_auth_provider),
) -> UndoLastMergeResponse:
    """Undo the last Master Profile merge (US168 / ADR-042).

    Restores the most recent pre-merge snapshot, clearing the conflicts that merge
    introduced. If edits occurred after the merge, the restore still proceeds but
    ``discarded_later_edits`` warns that those changes were dropped (coarse
    whole-profile restore; per-field revert deferred). Idempotent: a repeat call
    with nothing left to undo returns ``restored=false``.
    """
    await auth.get_current_user(request)
    result = await undo_last_merge(db)
    return UndoLastMergeResponse(
        restored=result.restored,
        discarded_later_edits=result.discarded_later_edits,
    )


@router.post("/import", response_model=MasterProfileResponse, status_code=status.HTTP_200_OK)
async def import_profile(
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
    file: Annotated[UploadFile | None, File(description="LinkedIn export ZIP")] = None,
    linkedin_json: Annotated[str | None, Form(description="LinkedIn export JSON string")] = None,
) -> MasterProfileResponse:
    """Structured data ingestor for LinkedIn/XING exports (ZIP or JSON).

    For CV file uploads (PDF, DOCX, images), use POST /api/profile/upload instead.
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

    try:
        if file is not None:
            file_bytes = await file.read()
            if _is_zip(file):
                coro = import_from_linkedin_zip(file_bytes, db, provider)
            elif _is_pdf(file):
                coro = import_from_linkedin_pdf(file_bytes, db, provider)
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
            coro = import_from_linkedin(parsed, db, provider)

        return await coro

    except HTTPException:
        raise
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
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
    auth: AuthProvider = Depends(get_auth_provider),
) -> dict[str, str]:
    """Upload a profile photo. Consent must be explicitly provided.

    Accepted formats: JPEG, PNG, WebP. Max 5 MB.
    Photo is stored and photo_url is set in the Master Profile personal_info.
    Re-uploading replaces the existing photo and refreshes consent_at.
    """
    user = await auth.get_current_user(request)
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
    auth: AuthProvider = Depends(get_auth_provider),
) -> None:
    """Delete the profile photo and clear GDPR consent."""
    user = await auth.get_current_user(request)
    try:
        await delete_photo(user_id=user.id, db=db, storage=storage)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/photo", status_code=status.HTTP_200_OK)
async def get_photo_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    auth: AuthProvider = Depends(get_auth_provider),
) -> Response:
    """Return the raw photo bytes (GDPR data portability). 404 if no photo on file."""
    user = await auth.get_current_user(request)
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
    _auth: AuthProvider = Depends(get_auth_provider),
) -> dict:
    """Lightweight check: returns exists + completeness_score (no full profile payload)."""
    return await profile_exists(db)


@router.get("", response_model=MasterProfileResponse, status_code=status.HTTP_200_OK)
async def get_current_profile(
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> MasterProfileResponse:
    profile = await get_profile(db)
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
    auth: AuthProvider = Depends(get_auth_provider),
) -> list[UploadHistoryItem]:
    """Return the last 10 uploads for the current user, newest first."""
    user = await auth.get_current_user(request)
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
    _auth: AuthProvider = Depends(get_auth_provider),
) -> list[EnrichmentRecord]:
    return await get_enrichment_history(db)


@router.get(
    "/changes",
    response_model=ProfileChangesResponse,
    status_code=status.HTTP_200_OK,
)
async def get_profile_changes_endpoint(
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> ProfileChangesResponse:
    """US145 / ADR-040 — the "what changed & why" surface data: the decision trail
    plus pending conflicts, read from the Master Profile only (retention-independent)."""
    return await get_profile_changes(db)


@router.get(
    "/health",
    response_model=ProfileHealthResponse,
    status_code=status.HTTP_200_OK,
)
async def get_profile_health_endpoint(
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> ProfileHealthResponse:
    """US160 (E033 / ADR-041 amended) — deterministic Profile Health: conflict +
    accuracy issues (severity-tagged) plus a completeness block. No LLM; reads
    only the durable Master Profile (never the 7-day upload — ADR-005)."""
    return await get_profile_health(db)


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def resolve_profile_conflict(
    conflict_id: str,
    body: ConflictResolutionRequest,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> MasterProfileResponse:
    try:
        return await resolve_conflict(conflict_id, body.resolution, body.value, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.patch("/{section}", response_model=MasterProfileResponse, status_code=status.HTTP_200_OK)
async def patch_section(
    section: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
    provider: LLMProvider = Depends(_get_provider),
) -> MasterProfileResponse:
    body = await request.json()
    try:
        return await patch_profile_section(section, body, db, provider=provider)
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
    auth: AuthProvider = Depends(get_auth_provider),
) -> dict:
    """GDPR Art. 17 — full user data erasure.

    Hard-deletes all user-scoped records in leaf-to-root FK order within a single
    transaction. Physical file deletion (uploads, PDFs) happens after commit so a
    storage I/O error cannot block the database erasure. Returns 202 Accepted.

    Cascade order:
      uploads → generated_cvs → interview_sessions → flow_sessions
      → applications → master_profiles → users
    job_analyses are NOT deleted (shared/global, no user_id).
    """
    from applire.models.application import Application
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.models.uploads import UploadRecord
    from applire.models.user import User

    user = await auth.get_current_user(request)
    uid = user.id
    now = datetime.now(timezone.utc)

    # --- Collect file paths before deleting rows ---
    upload_paths_result = await db.execute(
        select(UploadRecord.file_path).where(UploadRecord.user_id == uid)
    )
    upload_paths = [row[0] for row in upload_paths_result.fetchall()]

    # Collect profile photo path (single-user pattern; no user_id on MasterProfile)
    _photo_url_before_erasure: str | None = None
    _profile_snap_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    _profile_row = _profile_snap_result.scalar_one_or_none()
    if _profile_row:
        from applire.schemas.profile import MasterProfileData as _MPD
        _pdata = _MPD.model_validate(_profile_row.profile_json)
        _photo_url_before_erasure = _pdata.personal_info.photo_url

    # generated_cvs linked via profile_id → master_profiles
    cv_paths: list[str] = []  # PDF files not yet stored separately; no-op for now

    # --- Anonymised audit log (before any deletes) ---
    counts: dict[str, int] = {}

    async def _count_delete(model: Any, *where_clauses: Any) -> int:
        result = await db.execute(select(model).where(*where_clauses))
        rows = result.scalars().all()
        n = len(rows)
        for row in rows:
            db.expunge(row)
        return n

    # --- Atomic cascade (leaf → root) ---
    #
    # FK dependency graph (PostgreSQL enforces all):
    #   Application.flow_session_id  → flow_sessions.id   (circular with ↓)
    #   FlowSession.application_id   → applications.id    (circular with ↑)
    #   FlowSession.generated_cv_id  → generated_cvs.id
    #   FlowSession.interview_session_id → interview_sessions.id
    #   GeneratedCV.profile_id       → master_profiles.id
    #   InterviewSession.profile_id  → master_profiles.id
    #
    # Safe order: break the circular FK first, then delete referencing rows before
    # referenced rows.  Deletion order: uploads → (break cycle) → flow_sessions
    # → generated_cvs → interview_sessions → applications → master_profiles → users.
    try:
        # 1. uploads
        r = await db.execute(delete(UploadRecord).where(UploadRecord.user_id == uid))
        counts["uploads"] = r.rowcount

        # 2. Break Application ↔ FlowSession circular FK so each side can be deleted.
        #    Nullify Application.flow_session_id first so we can delete FlowSession rows
        #    without violating the Application.flow_session_id → flow_sessions.id FK.
        await db.execute(
            update(Application)
            .where(Application.user_id == uid)
            .values(flow_session_id=None)
        )

        # 3. flow_sessions — must come before generated_cvs and interview_sessions
        #    because FlowSession holds FKs into those tables; PostgreSQL blocks deleting
        #    a referenced row while any row in flow_sessions still points to it.
        r = await db.execute(delete(FlowSession).where(FlowSession.user_id == uid))
        counts["flow_sessions"] = r.rowcount

        # 4. generated_cvs (via profile_id subquery) — safe now that FlowSession is gone
        profile_ids_sq = select(MasterProfile.id)
        r = await db.execute(
            delete(GeneratedCV).where(GeneratedCV.profile_id.in_(profile_ids_sq))
        )
        counts["generated_cvs"] = r.rowcount

        # 5. interview_sessions — safe now that FlowSession is gone
        r = await db.execute(
            delete(InterviewSession).where(InterviewSession.profile_id.in_(profile_ids_sq))
        )
        counts["interview_sessions"] = r.rowcount

        # 5b. gap_analyses — GapAnalysis.profile_id → master_profiles.id FK (RESTRICT)
        #     must be deleted before master_profiles in step 7
        r = await db.execute(
            delete(GapAnalysis).where(GapAnalysis.profile_id.in_(profile_ids_sq))
        )
        counts["gap_analyses"] = r.rowcount

        # 5c. generated_cover_letters — profile_id → master_profiles.id FK (RESTRICT)
        #     safe to delete now that flow_sessions (which reference cover_letter_id) are gone
        r = await db.execute(
            delete(GeneratedCoverLetter).where(GeneratedCoverLetter.profile_id.in_(profile_ids_sq))
        )
        counts["generated_cover_letters"] = r.rowcount

        # 6. applications — safe now that FlowSession rows (which held application_id FKs)
        #    are deleted
        r = await db.execute(delete(Application).where(Application.user_id == uid))
        counts["applications"] = r.rowcount

        # 7. master_profiles — no user_id column; delete all (single-user deployment)
        r = await db.execute(delete(MasterProfile))
        counts["master_profiles"] = r.rowcount

        # NOTE: The User row is intentionally kept.  In Community Edition the stub
        # user (seeded once at startup) is required for the app to remain functional
        # after erasure.  All personal data has been deleted above; only the system
        # account record stays so subsequent uploads continue to work.
        counts["users"] = 0

        await db.commit()

    except Exception as exc:
        await db.rollback()
        logger.exception("GDPR erasure failed for user %s", uid, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erasure failed — no data was deleted. Please retry.",
        )

    # --- File deletion (outside transaction; failures are non-blocking) ---
    for path in upload_paths:
        try:
            await storage.delete(path)
        except Exception as exc:
            logger.warning("Failed to delete upload file %s: %s (will be reaped)", path, exc)

    # Delete profile photo (GDPR Art. 17)
    if _photo_url_before_erasure:
        try:
            await storage.delete(_photo_url_before_erasure)
        except Exception as exc:
            logger.warning(
                "Failed to delete photo file %s: %s (will be reaped)",
                _photo_url_before_erasure,
                exc,
            )

    logger.info(
        "GDPR erasure completed",
        extra={"event": "user_erasure_completed", "timestamp": now.isoformat(), "records": counts},
    )
    return {"message": "Erasure accepted", "records_deleted": counts}


@router.get("/export")
async def export_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> JSONResponse:
    """GDPR Art. 20 — data portability. Returns complete user data as JSON download.

    Excludes internal system state (raw_text_hash, token counts, etc.).
    """
    from applire.models.application import Application
    from applire.models.cv import GeneratedCV
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.models.uploads import UploadRecord

    user = await auth.get_current_user(request)
    uid = user.id

    # Profile — MasterProfile has no user_id column; use the same _get_latest pattern
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = profile_result.scalar_one_or_none()

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
