# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Signature image endpoints — upload / read / delete (#359).

A separate router rather than three more handlers on ``routers/profile.py``:
that file is already 950+ lines across CV upload, import, sections, health,
erasure and export, and the signature is a self-contained asset with one
service module behind it.

Mounted at the profile's own prefix so the URL reads as what it is — a property
of the profile, not a fifth top-level noun. No path parameter is used, so it
cannot collide with ``PATCH /api/profile/{section}`` (that door refuses
``signature_url`` with 422 anyway, exactly as it refuses ``photo_url``).

**No consent parameter**, deliberately — unlike ``POST /api/profile/photo``,
whose ``consent`` query flag discharges GDPR Art. 9(2)(a). Art. 9 is a closed
list and a signature image is not on it: it is personal data, and biometric
*material*, but it is not processed here for the purpose of uniquely identifying
a natural person (Art. 4(14)). See the ADR-063 amendment of 2026-09-11.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.services.signature import (
    delete_signature,
    get_signature_bytes,
    upload_signature,
)
from applire.storage import get_storage
from applire.storage.base import StorageProvider

router = APIRouter(prefix="/api/profile/signature", tags=["profile"])


def _get_storage() -> StorageProvider:
    return get_storage()


@router.post("", status_code=status.HTTP_200_OK)
async def upload_signature_endpoint(
    file: UploadFile,
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    auth: AuthProvider = Depends(get_auth_provider),
) -> dict[str, str]:
    """Store a signature image on the profile.

    Accepted formats: PNG (transparent background renders best), JPEG, WebP.
    Max 2 MB. Re-uploading replaces the existing image.
    """
    user = await auth.get_current_user(request)
    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    try:
        return await upload_signature(
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


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_signature_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    auth: AuthProvider = Depends(get_auth_provider),
) -> None:
    """Delete the stored signature image. Idempotent — no signature is a no-op."""
    user = await auth.get_current_user(request)
    try:
        await delete_signature(user_id=user.id, db=db, storage=storage)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("", status_code=status.HTTP_200_OK)
async def get_signature_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(_get_storage),
    auth: AuthProvider = Depends(get_auth_provider),
) -> Response:
    """Return the raw signature bytes. 404 when nothing is on file."""
    user = await auth.get_current_user(request)
    try:
        raw, media_type = await get_signature_bytes(
            user_id=user.id, db=db, storage=storage
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No signature on file"
        )
    # No-store: the bytes change in place under one stable URL, so a cached
    # response would show the previous signature after a replace.
    return Response(
        content=raw, media_type=media_type, headers={"Cache-Control": "no-store"}
    )
