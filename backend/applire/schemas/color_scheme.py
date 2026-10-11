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

# backend/applire/schemas/color_scheme.py
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_hex(v: str) -> str:
    if not _HEX_RE.match(v):
        raise ValueError(f"Invalid hex color: {v!r}. Expected #RRGGBB format.")
    return v.lower()


class ColorSchemeCreate(BaseModel):
    name: str
    seed_primary: str
    seed_accent: str
    seed_secondary: str
    surface_lightness: float = 0.97
    # US357: a new scheme is not offered to people until the admin says so.
    offered_to_users: bool = False

    @field_validator("seed_primary", "seed_accent", "seed_secondary", mode="before")
    @classmethod
    def validate_hex(cls, v: str) -> str:
        return _validate_hex(v)

    @field_validator("surface_lightness")
    @classmethod
    def validate_lightness(cls, v: float) -> float:
        if not 0.50 <= v <= 0.99:
            raise ValueError("surface_lightness must be between 0.50 and 0.99")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 64:
            raise ValueError("name must be 64 characters or fewer")
        return v


class ColorSchemePreviewRequest(BaseModel):
    seed_primary: str
    seed_accent: str
    seed_secondary: str
    surface_lightness: float = 0.97

    @field_validator("seed_primary", "seed_accent", "seed_secondary", mode="before")
    @classmethod
    def validate_hex(cls, v: str) -> str:
        return _validate_hex(v)

    @field_validator("surface_lightness")
    @classmethod
    def validate_lightness(cls, v: float) -> float:
        if not 0.50 <= v <= 0.99:
            raise ValueError("surface_lightness must be between 0.50 and 0.99")
        return v


class ColorSchemeResponse(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    is_builtin: bool
    offered_to_users: bool = False
    seed_primary: str
    seed_accent: str
    seed_secondary: str
    surface_lightness: float
    derived: dict[str, str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ActiveSchemeResponse(BaseModel):
    id: uuid.UUID
    name: str
    derived: dict[str, str]

    model_config = {"from_attributes": True}


class ColorSchemeOfferRequest(BaseModel):
    """``PATCH /api/admin/color-schemes/{id}/offer`` (US357, ruling CS-1)."""

    offered: bool


class OfferedSchemeResponse(BaseModel):
    """One entry of the person's picker (``GET /api/color-schemes``)."""

    id: uuid.UUID
    name: str
    seed_primary: str
    seed_accent: str
    seed_secondary: str
    derived: dict[str, str]
    is_default: bool


class SchemeChoiceResponse(BaseModel):
    """``GET /api/color-schemes`` — what the person may pick, and what applies.

    ``selected_id`` is the RESOLVED choice: ``None`` when the person follows the
    default, and also when the stored scheme was deleted or un-offered (CS-2) —
    the picker then shows "Instance default" as selected (SF-THEME.2).
    """

    default_id: uuid.UUID | None
    selected_id: uuid.UUID | None
    schemes: list[OfferedSchemeResponse]
