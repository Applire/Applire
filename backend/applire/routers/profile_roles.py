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

"""POST /api/profile/roles — add a new work entry to the Master Profile and
optionally close existing open roles. See spec
docs/superpowers/specs/2026-05-18-post-hire-profile-refresh-design.md
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.schemas.profile_roles import AddRoleRequest, AddRoleResponse
from applire.services.profile.role_add import (
    AddRoleValidationError,
    add_role_to_profile,
)

router = APIRouter(prefix="/api/profile/roles", tags=["profile"])


def _uid(user: "User | None"):
    """The resolved caller's id. ``require_user`` always yields a user (and sets
    the owner context to it); ``None`` only reaches here when a test calls the
    route function directly — the service then takes the owner context
    (ruling 3d-1), which is the same user on every real request."""
    return getattr(user, "id", None)


@router.post("", response_model=AddRoleResponse)
async def add_role(
    body: AddRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> AddRoleResponse:
    try:
        return await add_role_to_profile(body, db, user_id=_uid(current_user))
    except LookupError:
        raise HTTPException(status_code=404, detail="No master profile found")
    except AddRoleValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
