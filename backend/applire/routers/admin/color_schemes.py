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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_admin
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.color_scheme import (
    ActiveSchemeResponse,
    ColorSchemeCreate,
    ColorSchemePreviewRequest,
    ColorSchemeResponse,
)
from applire.services.color_schemes import (
    SchemeIsActive,
    SchemeIsBuiltin,
    SchemeNotFound,
    activate_scheme,
    create_scheme,
    delete_scheme,
    derive_scheme,
    get_active_scheme,
    list_schemes,
)

router = APIRouter(prefix="/api/admin/color-schemes", tags=["admin"])


# IMPORTANT: /active must be defined before /{scheme_id} to prevent FastAPI
# from matching "active" as a UUID path parameter.

@router.get("/active", response_model=ActiveSchemeResponse)
async def get_active(db: AsyncSession = Depends(get_db)):
    # Public by design (ADR-091 cl. 20 allowlist, MD-6): the login page themes itself.
    scheme = await get_active_scheme(db)
    if scheme is None:
        raise HTTPException(status_code=404, detail="No active color scheme found")
    return ActiveSchemeResponse(id=scheme.id, name=scheme.name, derived=scheme.derived)


@router.get("", response_model=list[ColorSchemeResponse])
async def list_all(
    db: AsyncSession = Depends(get_db), _auth: User = Depends(require_admin)
):
    return await list_schemes(db)


@router.post("/preview", response_model=dict)
async def preview(body: ColorSchemePreviewRequest, _auth: User = Depends(require_admin)):
    """Compute derived values without saving. Used by the editor for live preview."""
    return derive_scheme(
        body.seed_primary,
        body.seed_accent,
        body.seed_secondary,
        body.surface_lightness,
    )


@router.post("", response_model=ColorSchemeResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: ColorSchemeCreate,
    db: AsyncSession = Depends(get_db),
    _auth: User = Depends(require_admin),
):
    return await create_scheme(
        db,
        name=body.name,
        seed_primary=body.seed_primary,
        seed_accent=body.seed_accent,
        seed_secondary=body.seed_secondary,
        surface_lightness=body.surface_lightness,
    )


@router.patch("/{scheme_id}/activate", response_model=ColorSchemeResponse)
async def activate(
    scheme_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: User = Depends(require_admin),
):
    scheme = await activate_scheme(db, scheme_id)
    if scheme is None:
        raise HTTPException(status_code=404, detail="Color scheme not found")
    return scheme


@router.delete("/{scheme_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    scheme_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: User = Depends(require_admin),
):
    try:
        await delete_scheme(db, scheme_id)
    except SchemeNotFound:
        raise HTTPException(status_code=404, detail="Color scheme not found")
    except SchemeIsBuiltin:
        raise HTTPException(status_code=409, detail="Cannot delete a built-in scheme")
    except SchemeIsActive:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the active scheme — activate another scheme first",
        )
