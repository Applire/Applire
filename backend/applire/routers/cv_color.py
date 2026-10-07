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

"""PATCH /api/cv/{cv_id}/color — apply a user accent color override to a generated CV."""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.models.color_profile import ColorProfile
from applire.models.cv import CVGenerationStatus
from applire.services.color_detection import derive_tint

router = APIRouter(prefix="/api/cv", tags=["cv"])

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ColorOverrideRequest(BaseModel):
    accent_hex: str


class ColorOverrideResponse(BaseModel):
    color_profile_id: uuid.UUID
    derived: dict


async def apply_cv_color(
    cv_id: uuid.UUID,
    accent_hex: str,
    db: AsyncSession,
    *,
    user_id: "uuid.UUID | None" = None,
) -> dict:
    """Service logic — extracted for unit testability.

    ADR-092 cl. 6: the CV must be the caller's — a foreign id reads exactly like
    a missing one (S-10)."""
    if not _HEX_RE.match(accent_hex):
        raise ValueError(f"Invalid hex color: {accent_hex!r}. Must be #RRGGBB.")

    from applire.services.owner_resolution import owned_cv, resolve_owner

    owner = resolve_owner(user_id, site="cv_color.apply_cv_color")
    try:
        record = await owned_cv(db, cv_id, owner)
    except LookupError:
        raise LookupError(f"CV {cv_id} not found") from None
    if record.status != CVGenerationStatus.ready.value:
        raise LookupError(f"CV {cv_id} is not ready (status={record.status})")

    derived = {"--cv-accent": accent_hex, "--cv-accent-tint": derive_tint(accent_hex)}
    cp = ColorProfile(seed_primary=accent_hex, derived=derived, source="user")
    db.add(cp)
    await db.flush()

    record.color_profile_id = cp.id
    await db.commit()
    await db.refresh(cp)

    return {"color_profile_id": cp.id, "derived": derived}


@router.patch("/{cv_id}/color", response_model=ColorOverrideResponse)
async def patch_cv_color(
    cv_id: uuid.UUID,
    body: ColorOverrideRequest,
    db: AsyncSession = Depends(get_db),
    _auth: User = Depends(require_user),
) -> ColorOverrideResponse:
    try:
        result = await apply_cv_color(cv_id, body.accent_hex, db, user_id=_auth.id)
        return ColorOverrideResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
