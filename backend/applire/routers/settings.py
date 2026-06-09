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

"""GET/PATCH /api/settings — user preferences: default CV accent color and UI language."""
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.services.color_detection import _CE_STUB_USER_ID, derive_tint

router = APIRouter(prefix="/api/settings", tags=["settings"])

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_VALID_LANGUAGES = {"de", "en"}


class SettingsResponse(BaseModel):
    default_color_profile_id: uuid.UUID | None
    default_accent_hex: str | None
    ui_language: str


class SettingsPatchRequest(BaseModel):
    default_accent_hex: str | None = None
    ui_language: Literal["de", "en"] | None = None


async def get_settings(db: AsyncSession) -> dict:
    """Service logic — returns current settings for the CE stub user.

    ui_language is NOT NULL in the DB (ADR-038); defaults to 'en' if no row exists.
    """
    from applire.models.user_settings import UserSettings
    from applire.models.color_profile import ColorProfile

    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    row = result.scalar_one_or_none()

    # Build response
    ui_language = row.ui_language if row else "en"

    if row is None or row.default_color_profile_id is None:
        return {
            "default_color_profile_id": None,
            "default_accent_hex": None,
            "ui_language": ui_language,
        }

    cp = await db.get(ColorProfile, row.default_color_profile_id)
    if cp is None:
        return {
            "default_color_profile_id": None,
            "default_accent_hex": None,
            "ui_language": ui_language,
        }

    return {
        "default_color_profile_id": cp.id,
        "default_accent_hex": cp.seed_primary,
        "ui_language": ui_language,
    }


async def update_settings(
    db: AsyncSession,
    accent_hex: str | None = None,
    ui_language: str | None = None,
) -> dict:
    """Service logic — upsert user settings. Both fields are optional."""
    from applire.models.user_settings import UserSettings
    from applire.models.color_profile import ColorProfile

    if accent_hex is not None and not _HEX_RE.match(accent_hex):
        raise ValueError(f"Invalid hex color: {accent_hex!r}. Must be #RRGGBB.")

    if ui_language is not None and ui_language not in _VALID_LANGUAGES:
        raise ValueError(
            f"Invalid ui_language: {ui_language!r}. Must be one of {_VALID_LANGUAGES}."
        )

    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = UserSettings(user_id=_CE_STUB_USER_ID)
        db.add(row)

    if accent_hex is not None:
        derived = {"--cv-accent": accent_hex, "--cv-accent-tint": derive_tint(accent_hex)}
        cp = ColorProfile(seed_primary=accent_hex, derived=derived, source="user")
        db.add(cp)
        await db.flush()
        row.default_color_profile_id = cp.id

    if ui_language is not None:
        row.ui_language = ui_language

    await db.commit()

    response: dict = {"ui_language": row.ui_language or "en"}
    if row.default_color_profile_id:
        cp = await db.get(ColorProfile, row.default_color_profile_id)
        response["default_color_profile_id"] = cp.id if cp else None
        response["default_accent_hex"] = cp.seed_primary if cp else None
    else:
        response["default_color_profile_id"] = None
        response["default_accent_hex"] = None

    return response


@router.get("", response_model=SettingsResponse)
async def api_get_settings(
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SettingsResponse:
    result = await get_settings(db)
    return SettingsResponse(**result)


@router.patch("", response_model=SettingsResponse)
async def api_patch_settings(
    body: SettingsPatchRequest,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SettingsResponse:
    try:
        result = await update_settings(
            db,
            accent_hex=body.default_accent_hex,
            ui_language=body.ui_language,
        )
        return SettingsResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
