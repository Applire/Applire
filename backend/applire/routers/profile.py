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

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, status

from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: The catch-all 500 text (same wording as routers/session.py's #256 body).
_INTERNAL_ERROR_MESSAGE = "An unexpected error occurred. Please try again."

from applire.auth.deps import require_user
from applire.constants import MAX_CV_UPLOAD_BYTES
from applire.models.user import User
from applire.db.session import get_db
from applire.exceptions import ReconcileFailedError
from applire.ocr import get_ocr_extractor, image_import_available
from applire.ocr.base import CVImageExtractor
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.models.uploads import UploadRecord
from applire.schemas.profile import (
    ConflictResolutionRequest,
    MergeProposalResponse,
    CVImportJobListItem,
    CVImportJobResponse,
    CVImportStatusResponse,
    CVUploadResponse,
    EnrichmentRecord,
    MasterProfileResponse,
    PhotoCrop,
    ProfileChangesResponse,
    ImportReceiptSummary,
    ReceiptSummary,
    ProfileHealthResponse,
    SeparateMatchRequest,
    ResolveConfirmationRequest,
    WithdrawDenialRequest,
    StagedResolveRequest,
    StagedResolveResponse,
    UndoLastMergeRequest,
    UndoLastMergeResponse,
    UploadHistoryItem,
)
from applire.schemas.testimony import TestimonyRequest, TestimonyResult
from applire.schemas.entry_merge import EntryMergeRequest, KeepSeparateRequest
from applire.services.profile.entry_merge import (
    DatesChoiceRequired,
    MergedTextRequired,
    SameEntry,
    StalePreview,
    entry_merge,
    keep_entries_separate,
)
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
    get_receipt_summary,
    get_profile_health,
    get_profile,
    patch_profile_section,
    profile_exists,
    propose_conflict_merge,
    resolve_conflict,
    resolve_confirmation,
    MatchNotSeparable,
    resolve_staged_extraction,
    separate_match,
    confirm_match,
    withdraw_denial,
    DenialNotFound,
    get_import_summary,
    apply_restated,
    RestatedNotApplicable,
    record_availability,
    dismiss_availability_reminder,
    StagedExtractionAlreadyResolved,
    StagedExtractionNotFound,
)
from applire.storage import get_storage
from applire.storage.base import StorageProvider
from applire.services.photo import delete_photo, get_photo_bytes, set_photo_crop, upload_photo
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

# R2-13 (Hazelnut build 4) — the reconcile call that merges a CV failed after
# its one retry (`ReconcileFailedError`). Nothing was changed; the same action
# can be repeated. `detail.error` carries the stable code the UI localizes.
_NOT_SAVED_USER_MESSAGE = (
    "We couldn't merge this CV into your profile this time. "
    "Nothing was changed — please try again."
)

# Clean, user-appropriate message for an LLM timeout on upload/import (ADR-047 §4).
# Never leak the raw provider text ("timed out after 120s", model/provider name).
_TIMEOUT_USER_MESSAGE = (
    "This took longer than expected and didn't finish. "
    "Nothing was changed — please try uploading it again."
)


# #789 L12 — an image or scanned PDF on an instance whose OCR backend cannot run.
_OCR_UNAVAILABLE_USER_MESSAGE = (
    "This instance can't read images or scanned documents (no OCR backend is configured). "
    "Upload a text-based PDF, DOCX or plain-text file instead."
)


def _get_provider() -> LLMProvider:
    return get_provider()


def _get_storage() -> StorageProvider:
    return get_storage()


def _get_ocr() -> CVImageExtractor:
    return get_ocr_extractor()


async def _read_capped(file: UploadFile) -> bytes:
    """Read an uploaded import file, refusing one over ``MAX_CV_UPLOAD_BYTES`` (#789 L13).

    The upload surfaces promise "up to 10 MB"; this makes the promise true on every REST
    import door (it used to hold only on the MCP door, and behind nginx's 20 M cap).
    Reads one byte past the limit so an oversized body is detected without buffering it
    whole, and answers 413 with a stable ``error`` code the frontend translates.
    """
    data = await file.read(MAX_CV_UPLOAD_BYTES + 1)
    if len(data) > MAX_CV_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "message": f"This file is larger than {MAX_CV_UPLOAD_BYTES // (1024 * 1024)} MB.",
                "max_bytes": MAX_CV_UPLOAD_BYTES,
            },
        )
    return data


#: Ruling V-4 = A (Hazelnut build 2, 783:L68 / 789:L31): the synchronous REST
#: import doors are retired before 1.0. They stay registered (a caller gets 410
#: and a pointer, not a 404/405) and keep their auth dependency (the route
#: inventory requires one); the work runs on the async import job only.
_RETIRED_IMPORT_DOOR = {
    "error": "endpoint_retired",
    "message": (
        "This synchronous import door was removed before 1.0. Use POST "
        "/api/profile/import-jobs (202, then poll GET /api/profile/import-jobs/"
        "{import_id}); a LinkedIn export (ZIP, or PDF/JSON with ?source=linkedin) "
        "uses the same door."
    ),
    "use": "/api/profile/import-jobs",
}


@router.post("/upload", status_code=status.HTTP_410_GONE)
async def upload_cv_endpoint(current_user: User = Depends(require_user)) -> JSONResponse:
    """RETIRED (ruling V-4 = A) — 410 Gone with a pointer to ``POST /import-jobs``.

    The synchronous door blocked for the whole ingest; a reverse proxy cut it at its
    read timeout (300 s in the shipped nginx config) while the ingest kept running and
    wrote the vault minutes later, outside the per-user import lock (#674, measured on
    a self-hosted instance 2026-09-17).
    """
    return JSONResponse(status_code=status.HTTP_410_GONE, content=_RETIRED_IMPORT_DOOR)


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
    source: Literal["linkedin"] | None = Query(
        default=None,
        description=(
            "Set to ``linkedin`` to import a LinkedIn profile-PDF export through the "
            "LinkedIn adapter instead of the CV one. A ZIP needs no hint: it is always "
            "a LinkedIn export (#789 L31)."
        ),
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

    The LinkedIn export rides the same job (#789 L31): a ``.zip`` runs the LinkedIn ZIP
    adapter, a PDF with ``?source=linkedin`` the LinkedIn PDF adapter; the result is the
    same ``CVUploadResponse`` shape (including the GATED hold), so the poll and the
    pre-merge gate are unchanged.
    """
    user = current_user
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    file_bytes = await _read_capped(file)

    job = await create_import_job(db, filename=filename, user_id=user.id)
    background_tasks.add_task(
        run_import_job_background,
        job.id,
        file_bytes,
        filename,
        content_type,
        job_id,
        user.id,
        source=source,
    )
    return CVImportJobResponse(import_id=job.id, status=CVImportStatus(job.status))


class ImportCapabilitiesResponse(BaseModel):
    """What this instance's import doors can read (#789 L12). One boolean per
    capability; nothing about keys or configuration beyond that bit."""

    image_import: bool


@router.get(
    "/import-capabilities",
    response_model=ImportCapabilitiesResponse,
    status_code=status.HTTP_200_OK,
)
async def get_import_capabilities_endpoint(
    current_user: User = Depends(require_user),
) -> ImportCapabilitiesResponse:
    """Whether image CVs can be read here (``OCR_BACKEND`` runnable). The upload
    surfaces drop the image hint and accept-filter when this is false."""
    return ImportCapabilitiesResponse(image_import=image_import_available())


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
    except ReconcileFailedError:
        # R2-13 — the merge was not saved; the staged item stays held (it is
        # marked resolved only after the merge commits), so "merge" can be
        # pressed again.
        logger.warning("staged resolve %s: reconcile failed; nothing merged", staged_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "not_saved", "message": _NOT_SAVED_USER_MESSAGE},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post(
    "/undo-last-merge",
    response_model=UndoLastMergeResponse,
    status_code=status.HTTP_200_OK,
)
async def undo_last_merge_endpoint(
    request: Request,
    body: UndoLastMergeRequest | None = None,
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
    from applire.services.profile.snapshots import UndoStale

    try:
        result = await undo_last_merge(
            db,
            user_id=_uid(current_user),
            expected_updated_at=body.expected_updated_at if body is not None else None,
        )
    except UndoStale:
        # ADV-8 — the merge the client named is no longer the last write.
        raise HTTPException(status_code=409, detail="undo_stale")
    return UndoLastMergeResponse(
        restored=result.restored,
        discarded_later_edits=result.discarded_later_edits,
    )


@router.post(
    "/matches/separate",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def separate_match_endpoint(
    body: SeparateMatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """#717 — "Nicht dasselbe": undo one recognised-as-already-present match
    (ADR-063 amended 2026-10-07, ruling V-2 = A). Removes the alternate names
    that match recorded and adds the incoming entry as its own entry, both in
    one history record. 409 when the receipt is unknown, already undone, or a
    language-name-table match (a fact, not undoable); 404 without a profile.
    """
    try:
        return await separate_match(
            db, entity_id=body.entity_id, incoming=body.incoming, user_id=_uid(current_user)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="No profile found")
    except MatchNotSeparable as exc:
        raise HTTPException(status_code=409, detail=exc.code)


@router.post(
    "/matches/confirm",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def confirm_match_endpoint(
    body: SeparateMatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """FS-6 (ruling V-4 = A) — "Ja": a recognised pair IS the same entry; the
    import summary stops asking. Same body and 409 codes as /matches/separate."""
    try:
        return await confirm_match(
            db, entity_id=body.entity_id, incoming=body.incoming, user_id=_uid(current_user)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="No profile found")
    except MatchNotSeparable as exc:
        raise HTTPException(status_code=409, detail=exc.code)


@router.post("/entries/merge", status_code=status.HTTP_200_OK, response_model=None)
async def merge_entries_endpoint(
    body: EntryMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    provider: LLMProvider = Depends(_get_provider),
):
    """Hazelnut build 5 (U-11) — merge two entries of one section. ``preview``
    writes nothing and returns the merged entry, the aliases, a date conflict
    and the duplicate bullets; ``commit`` re-validates the candidate's choices
    and writes one history record with a pre-write snapshot (undo-last-merge).
    404 unknown entry / no profile; 409 ``stale_preview``; 422
    ``dates_choice_required`` / same entry."""
    try:
        return await entry_merge(db, body, provider, user_id=_uid(current_user))
    except LookupError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except SameEntry:
        raise HTTPException(status_code=422, detail="same_entry")
    except StalePreview:
        raise HTTPException(status_code=409, detail="stale_preview")
    except DatesChoiceRequired:
        raise HTTPException(status_code=422, detail="dates_choice_required")
    except MergedTextRequired:
        raise HTTPException(status_code=422, detail="merged_text_required")


@router.post(
    "/entries/keep-separate",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def keep_entries_separate_endpoint(
    body: KeepSeparateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """Hazelnut build 5 (U-9) — "Nein, getrennt": the two entries stay separate
    and the duplicate-entry question is not asked again (idempotent)."""
    try:
        return await keep_entries_separate(
            db, body.section, body.entry_ids, user_id=_uid(current_user)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except SameEntry:
        raise HTTPException(status_code=422, detail="same_entry")


@router.post(
    "/denials/withdraw",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def withdraw_denial_endpoint(
    body: WithdrawDenialRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """G-5 (ADR-059 amended 2026-10-24) — the candidate withdraws one of their
    denials after the confirm dialog. 404 = no profile; 409 ``denial_not_found``
    = no active denial of that name (nothing written)."""
    try:
        return await withdraw_denial(db, concept=body.concept, user_id=_uid(current_user))
    except DenialNotFound:
        raise HTTPException(status_code=409, detail="denial_not_found")
    except LookupError:
        raise HTTPException(status_code=404, detail="No profile found")


@router.post(
    "/confirmations/resolve",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def resolve_confirmation_endpoint(
    body: ResolveConfirmationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """G-5 / ruling V-5 (c) — the candidate answers a parked confirmation in
    the Health hub (the ``denial_scope`` family: "Nein, der Lebenslauf stimmt:
    Angabe zurücknehmen" IS the withdrawal). 409 ``confirmation_not_found`` =
    no open ask by that id (already answered, or no profile); nothing written."""
    try:
        return await resolve_confirmation(
            body.confirmation_id, body.chosen_option, db, user_id=_uid(current_user)
        )
    except LookupError:
        raise HTTPException(status_code=409, detail="confirmation_not_found")


class AvailabilityRequest(BaseModel):
    text: str


@router.put(
    "/availability",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def put_availability_endpoint(
    body: AvailabilityRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """D-B3V7 (rulings 787:L3 + V-4) — set the notice period / earliest start
    from My Profile (source ``manual_edit``); an empty text clears it."""
    if len(body.text) > 500:
        raise HTTPException(status_code=422, detail="text_too_long")
    try:
        return await record_availability(
            db, text=body.text, source="manual_edit", user_id=_uid(current_user)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="No profile found")


@router.post(
    "/availability/reminder/dismiss",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def dismiss_availability_reminder_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """D-B3V7 (ruling V-4) — close the dashboard reminder, server-side."""
    try:
        return await dismiss_availability_reminder(db, user_id=_uid(current_user))
    except LookupError:
        raise HTTPException(status_code=404, detail="No profile found")


class ApplyRestatedRequest(BaseModel):
    entity_id: str
    field: Literal["responsibilities", "achievements"]
    text: str


@router.post(
    "/restated/apply",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def apply_restated_endpoint(
    body: ApplyRestatedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """D-B3V6 (ruling V-3 = A) — "Trotzdem übernehmen": write one bullet the
    import restatement check held back. 409 ``unknown`` when no restated
    receipt on that entry names the text, ``entity_gone`` when the entry no
    longer exists; 404 without a profile."""
    try:
        return await apply_restated(
            db, entity_id=body.entity_id, field=body.field, text=body.text,
            user_id=_uid(current_user),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="No profile found")
    except RestatedNotApplicable as exc:
        raise HTTPException(status_code=409, detail=exc.code)


@router.post("/import", status_code=status.HTTP_410_GONE)
async def import_profile(current_user: User = Depends(require_user)) -> JSONResponse:
    """RETIRED (ruling V-4 = A) — 410 Gone with a pointer to ``POST /import-jobs``.

    The LinkedIn/XING export door ran the full ingest synchronously since #367 and was
    exposed to the same proxy cut as ``/upload``. A ZIP is detected by the async job; a
    LinkedIn PDF or JSON export takes ``?source=linkedin`` there.
    """
    return JSONResponse(status_code=status.HTTP_410_GONE, content=_RETIRED_IMPORT_DOOR)


# ---------------------------------------------------------------------------
# Photo endpoints
# ---------------------------------------------------------------------------


@router.post("/photo", status_code=status.HTTP_200_OK)
async def upload_photo_endpoint(
    file: UploadFile,
    request: Request,
    consent: bool = Query(default=False, description="Must be True — GDPR Art. 9(2)(a) explicit consent"),
    cx: float | None = Query(default=None, ge=0.0, le=1.0, description="#781 framing: crop centre x (fraction of width)"),
    cy: float | None = Query(default=None, ge=0.0, le=1.0, description="#781 framing: crop centre y (fraction of height)"),
    zoom: float | None = Query(default=None, ge=1.0, le=4.0, description="#781 framing: zoom (1 = largest 4:5 crop)"),
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    """Upload a profile photo. Consent must be explicitly provided.

    Accepted formats: JPEG, PNG, WebP. Max 5 MB.
    Photo is stored and photo_url is set in the Master Profile personal_info.
    Re-uploading replaces the existing photo and refreshes consent_at.

    #781 (ruling F-2): the file is stored unmodified; ``cx``/``cy``/``zoom``
    (all three or none) are the framing chosen for it in the adjuster. Without
    them the new photo starts unframed.
    """
    user = current_user
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consent is required to store your photo (GDPR Art. 9).",
        )
    given = [v is not None for v in (cx, cy, zoom)]
    if any(given) and not all(given):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Send the framing as cx, cy and zoom together, or none of them.",
        )
    crop = PhotoCrop(cx=cx, cy=cy, zoom=zoom) if all(given) else None
    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    try:
        return await upload_photo(
            user_id=user.id,
            file_bytes=file_bytes,
            content_type=content_type,
            db=db,
            storage=storage,
            crop=crop,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.put(
    "/photo/crop",
    status_code=status.HTTP_200_OK,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": PhotoCrop.model_json_schema()}},
        }
    },
)
async def set_photo_crop_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """#781 (ruling F-2) — store the framing of the photo on file.

    The stored original is untouched; every CV template and the Word export
    apply this framing at render, so re-framing is lossless and takes effect on
    every document's next download. 404 when there is no photo to frame.

    The body is validated here rather than by a typed parameter: JSON allows a
    bare ``NaN``/``Infinity``, and the framework's 422 echoes the offending input,
    which cannot be encoded back to JSON — the candidate got a 500 instead of a
    422 (adversarial pass 2026-10-17). The 422 below names the rule, not the value.
    """
    try:
        raw = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="The framing must be a JSON object.")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="The framing must be a JSON object.")
    try:
        body = PhotoCrop.model_validate(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="The framing needs cx and cy between 0 and 1 and zoom between 1 and 4.",
        )
    try:
        return await set_photo_crop(user_id=current_user.id, crop=body, db=db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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
    "/changes/summary",
    response_model=ReceiptSummary,
    status_code=status.HTTP_200_OK,
)
async def get_receipt_summary_endpoint(
    session_id: str | None = Query(
        default=None,
        max_length=64,
        description="The interview session to summarise; the latest one when omitted.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ReceiptSummary:
    """ADR-040 amended 2026-10-22 (ruling V-1 = A) — the interview receipt as a
    summary of one session: one fact per line, grouped per section, the key
    points first. A view over the trail; the trail itself is unchanged."""
    return await get_receipt_summary(db, session_id=session_id, user_id=_uid(current_user))


@router.get(
    "/import-summary",
    response_model=ImportReceiptSummary,
    status_code=status.HTTP_200_OK,
)
async def get_import_summary_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ImportReceiptSummary:
    """R2-8 (9) (ruling V-2 = A) — the last import in the user's terms: what it
    added, the pairs it recognised (FS-6 pair questions), what it kept apart
    and what it held back. A view over the trail; read-only."""
    return await get_import_summary(db, user_id=_uid(current_user))


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


# ── UAT-3d — the hub's writing one-action resolvers ─────────────────────────
from applire.schemas.profile import AdoptNotAppliedRequest, AspirationAnswerRequest, HealthUnitRequest  # noqa: E402
from applire.services.profile.hub_actions import (  # noqa: E402
    HubEntryNotFound,
    NotAppliedConflict,
    NotAppliedUnprocessable,
    UnitNotApplicable,
    adopt_not_applied_item,
    set_budget_unit,
)


@router.post(
    "/health/unit",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def set_health_unit_endpoint(
    body: HealthUnitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """UAT-3d — the unit input in place: writes the stored budget wording plus
    the chosen currency onto that work entry. 404 unknown entry (or no profile);
    422 when the entry has no budget value or it already states a unit."""
    try:
        return await set_budget_unit(
            db, entry_id=body.entry_id, unit=body.unit, user_id=_uid(current_user)
        )
    except (LookupError, HubEntryNotFound):
        raise HTTPException(status_code=404, detail="Entry not found")
    except UnitNotApplicable as exc:
        raise HTTPException(status_code=422, detail=str(exc))


from applire.services.profile.aspiration import (  # noqa: E402 — RULING B-1
    AspirationAnswered,
    AspirationOutcomeRequired,
    answer_aspiration,
)


@router.post(
    "/aspirations/answer",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def answer_aspiration_endpoint(
    body: AspirationAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """RULING B-1 — "Ist das erreicht?" for one ``aspiration:<entry>:<key>``
    issue. ``achieved`` + ``outcome`` writes the outcome as an achievement in
    the candidate's words; not achieved keeps the bullet as a goal. 404 unknown
    issue (or no profile); 409 already answered; 422 achieved without outcome."""
    try:
        return await answer_aspiration(
            db,
            issue_id=body.issue_id,
            achieved=body.achieved,
            outcome=body.outcome,
            user_id=_uid(current_user),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Issue not found")
    except AspirationAnswered:
        raise HTTPException(status_code=409, detail="already_answered")
    except AspirationOutcomeRequired:
        raise HTTPException(status_code=422, detail="outcome_required")


@router.post(
    "/not-applied/adopt",
    response_model=MasterProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def adopt_not_applied_endpoint(
    body: AdoptNotAppliedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """UAT-3d — "Übernehmen": take one not-applied import item into the vault.
    404 unknown issue/item; 409 already adopted; 422 not adoptable (no entry
    was captured — a receipt from before the capture, or a scalar item)."""
    try:
        return await adopt_not_applied_item(
            db, issue_id=body.issue_id, item_key=body.item_key, user_id=_uid(current_user)
        )
    except (LookupError, HubEntryNotFound):
        raise HTTPException(status_code=404, detail="Issue or item not found")
    except NotAppliedConflict:
        raise HTTPException(status_code=409, detail="already_adopted")
    except NotAppliedUnprocessable:
        raise HTTPException(status_code=422, detail="not_adoptable")


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
    except Exception:
        logger.exception("profile route failed")  # never echo str(exc) (MD-24 (6))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=_INTERNAL_ERROR_MESSAGE
        )


@router.post(
    "/conflicts/{conflict_id}/merge-proposal",
    response_model=MergeProposalResponse,
    status_code=status.HTTP_200_OK,
)
async def conflict_merge_proposal(
    conflict_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    provider: LLMProvider = Depends(_get_provider),
) -> MergeProposalResponse:
    """ADR-099 cl. 11 — one model call, verified, nothing written. 404 for an
    unknown/closed dispute, 422 for one that offers no merge."""
    try:
        p = await propose_conflict_merge(conflict_id, db, provider, user_id=_uid(current_user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception:
        logger.exception("profile route failed")  # never echo str(exc) (MD-24 (6))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=_INTERNAL_ERROR_MESSAGE
        )
    return MergeProposalResponse(proposal=p.proposal, verifier=p.verifier, sources=list(p.sources))


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
    except VaultWriteRevertedError as exc:
        # The same structured body the upload doors give (#597): before W3 this
        # door surfaced it only through the catch-all's str(exc), which no
        # longer echoes exception text (MD-24 (6)).
        logger.error("patch_section: vault write reverted (%s)", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "vault_write_reverted", "message": _TRUNCATION_USER_MESSAGE},
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
    except Exception:
        # Collector (MD-24 (6)): never echo str(exc) — it can carry SQL, a table
        # name (an owner-guard refusal) or provider payload. Logged in full here.
        logger.exception("profile route failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_INTERNAL_ERROR_MESSAGE,
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
