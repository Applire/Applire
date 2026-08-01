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
from pydantic import BaseModel, Field
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
    # ADR-038 (amended 2026-08-01, #400): True only when a write explicitly
    # carried ui_language — the served 'en' default is not a choice.
    ui_language_explicit: bool = False
    hide_predownload_notice: bool
    # E042/US236 (ADR-051 §1): NULL = "use region standard".
    target_cv_pages: int | None = None


class SettingsPatchRequest(BaseModel):
    default_accent_hex: str | None = None
    ui_language: Literal["de", "en"] | None = None
    hide_predownload_notice: bool | None = None
    # E042/US236: >= 1, no upper cap (users may deliberately exceed the norm).
    target_cv_pages: int | None = Field(default=None, ge=1)


async def get_settings(db: AsyncSession) -> dict:
    """Service logic — returns current settings for the CE stub user.

    ui_language is nullable (ADR-038 amended 2026-08-01): NULL = never chosen,
    served as 'en' with ui_language_explicit=False so the frontend can
    materialise its locale as an explicit choice (#400).
    """
    from applire.models.user_settings import UserSettings
    from applire.models.color_profile import ColorProfile

    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    row = result.scalar_one_or_none()

    # Build response
    ui_language_explicit = bool(row.ui_language) if row else False
    ui_language = (row.ui_language if row else None) or "en"
    hide_predownload_notice = bool(row.hide_predownload_notice) if row else False
    target_cv_pages = row.target_cv_pages if row else None

    if row is None or row.default_color_profile_id is None:
        return {
            "default_color_profile_id": None,
            "default_accent_hex": None,
            "ui_language": ui_language,
            "ui_language_explicit": ui_language_explicit,
            "hide_predownload_notice": hide_predownload_notice,
            "target_cv_pages": target_cv_pages,
        }

    cp = await db.get(ColorProfile, row.default_color_profile_id)
    if cp is None:
        return {
            "default_color_profile_id": None,
            "default_accent_hex": None,
            "ui_language": ui_language,
            "ui_language_explicit": ui_language_explicit,
            "hide_predownload_notice": hide_predownload_notice,
            "target_cv_pages": target_cv_pages,
        }

    return {
        "default_color_profile_id": cp.id,
        "default_accent_hex": cp.seed_primary,
        "ui_language": ui_language,
        "ui_language_explicit": ui_language_explicit,
        "hide_predownload_notice": hide_predownload_notice,
        "target_cv_pages": target_cv_pages,
    }


async def update_settings(
    db: AsyncSession,
    accent_hex: str | None = None,
    ui_language: str | None = None,
    hide_predownload_notice: bool | None = None,
    target_cv_pages: int | None = None,
    clear_target_cv_pages: bool = False,
) -> dict:
    """Service logic — upsert user settings. All fields are optional.

    target_cv_pages=None means "not provided" (leave untouched), matching the
    other optional fields. To explicitly clear a stored value back to NULL
    ("use region standard"), pass clear_target_cv_pages=True — the caller
    (the PATCH route) is responsible for distinguishing an explicit-null
    request body field from an omitted one via model_fields_set.
    """
    from applire.models.user_settings import UserSettings
    from applire.models.color_profile import ColorProfile

    if accent_hex is not None and not _HEX_RE.match(accent_hex):
        raise ValueError(f"Invalid hex color: {accent_hex!r}. Must be #RRGGBB.")

    if ui_language is not None and ui_language not in _VALID_LANGUAGES:
        raise ValueError(
            f"Invalid ui_language: {ui_language!r}. Must be one of {_VALID_LANGUAGES}."
        )

    if target_cv_pages is not None and target_cv_pages < 1:
        raise ValueError(
            f"Invalid target_cv_pages: {target_cv_pages!r}. Must be >= 1."
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

    if hide_predownload_notice is not None:
        row.hide_predownload_notice = hide_predownload_notice

    if clear_target_cv_pages:
        row.target_cv_pages = None
    elif target_cv_pages is not None:
        row.target_cv_pages = target_cv_pages

    await db.commit()

    response: dict = {
        "ui_language": row.ui_language or "en",
        "ui_language_explicit": bool(row.ui_language),
        "hide_predownload_notice": bool(row.hide_predownload_notice),
        "target_cv_pages": row.target_cv_pages,
    }
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
    # Distinguish an explicit {"target_cv_pages": null} (clear the stored
    # value → "use region standard") from an omitted key (leave untouched).
    # Pydantic only exposes this via model_fields_set — body.target_cv_pages
    # alone is ambiguous between the two (both are None).
    clear_target_cv_pages = (
        "target_cv_pages" in body.model_fields_set and body.target_cv_pages is None
    )
    try:
        result = await update_settings(
            db,
            accent_hex=body.default_accent_hex,
            ui_language=body.ui_language,
            hide_predownload_notice=body.hide_predownload_notice,
            target_cv_pages=body.target_cv_pages,
            clear_target_cv_pages=clear_target_cv_pages,
        )
        return SettingsResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
