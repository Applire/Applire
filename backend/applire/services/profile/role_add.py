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

"""Work-entry mutation logic for adding a new role to a master profile.

Two public entry points:

- ``apply_add_role(profile, req)`` — pure, no DB.  Validates and mutates an
  in-memory ``MasterProfileData``; raises ``AddRoleValidationError`` on any
  constraint violation before touching the profile.

- ``add_role_to_profile(req, db)`` — DB-aware.  Loads the latest
  ``MasterProfile`` row, calls ``apply_add_role``, persists the result via
  ``db.commit()``, and returns an ``AddRoleResponse``.  Shared by the REST
  router (``POST /api/profile/roles``) and the MCP ``add_role`` tool.
  Raises ``LookupError`` when no profile exists and ``AddRoleValidationError``
  when the request cannot be applied.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.profile import MasterProfile
from applire.schemas.profile import (
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ProfileMetadata,
    WorkEntry,
)
from applire.schemas.profile_roles import AddRoleRequest, AddRoleResponse
from applire.services.profile.role_facts import project_role_facts


class AddRoleValidationError(ValueError):
    """Raised when the request cannot be applied (router should map to HTTP 422)."""


@dataclass
class AddRoleResult:
    profile: MasterProfileData
    new_role_id: str
    closed_role_ids: list[str]


def apply_add_role(profile: MasterProfileData, req: AddRoleRequest) -> AddRoleResult:
    """Apply the request to the profile in-place-style and return the result.

    Validation is all-or-nothing: any failure raises AddRoleValidationError
    before any mutation, so the caller never sees a partial profile.
    """
    # Validate close_roles
    by_id: dict[str, WorkEntry] = {w.id: w for w in profile.work_experience}
    for entry in req.close_roles:
        we = by_id.get(entry.role_id)
        if we is None:
            raise AddRoleValidationError(f"unknown role_id: {entry.role_id}")
        if we.end_date is not None:
            raise AddRoleValidationError(f"role_id {entry.role_id} is not open")
        if entry.end_date > req.start_date:
            raise AddRoleValidationError(
                f"end_date {entry.end_date} must be on or before new start_date {req.start_date}"
            )

    # Mutate
    new_entry = WorkEntry(
        company=req.company,
        role=req.title,
        location=req.location,
        start_date=req.start_date,
        end_date=None,
        is_current=True,  # #155 — a just-started role IS the current position
        industry_context=req.industry,
    )
    # #328 option 4 — this door constructs a WorkEntry directly rather than
    # through the op applier, so it must project too: every persisted entry
    # carries an honest provenance map, or the marking means nothing.
    project_role_facts(new_entry)
    profile.work_experience.insert(0, new_entry)

    closed_ids: list[str] = []
    for entry in req.close_roles:
        by_id[entry.role_id].end_date = entry.end_date
        by_id[entry.role_id].is_current = False  # #155 — known ended
        closed_ids.append(entry.role_id)

    # Audit
    if profile.metadata is None:
        profile.metadata = ProfileMetadata()

    changes: list[FieldChange] = [
        FieldChange(
            section="work_experience",
            field=f"[{new_entry.id}]",
            action="added",
            new_value={"company": new_entry.company, "role": new_entry.role,
                       "start_date": new_entry.start_date},
        )
    ]
    for entry in req.close_roles:
        changes.append(
            FieldChange(
                section="work_experience",
                field=f"[{entry.role_id}].end_date",
                action="updated",
                old_value=None,
                new_value=entry.end_date,
            )
        )
    profile.metadata.enrichment_history.append(
        EnrichmentRecord(
            timestamp=datetime.now(timezone.utc),
            source="manual_role_add",
            changes=changes,
        )
    )
    profile.metadata.last_updated = datetime.now(timezone.utc)

    return AddRoleResult(
        profile=profile,
        new_role_id=new_entry.id,
        closed_role_ids=closed_ids,
    )


async def add_role_to_profile(req: AddRoleRequest, db: AsyncSession) -> AddRoleResponse:
    """Load latest profile, apply the add-role request, persist, and return the response.

    Shared by POST /api/profile/roles and the MCP add_role tool.
    Raises LookupError (no profile) and AddRoleValidationError (invalid request).
    """
    result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError("No master profile found")

    profile_data = MasterProfileData.model_validate(record.profile_json)
    outcome = apply_add_role(profile_data, req)  # raises AddRoleValidationError

    record.profile_json = outcome.profile.model_dump(mode="json")
    await db.commit()

    # TODO US179: manually-added roles get the lean-floor expectation set until a
    # provider is threaded here (fast-follow). Floor fallback is safe (under-asks).
    return AddRoleResponse(
        profile_id=str(record.id),
        new_role_id=outcome.new_role_id,
        closed_role_ids=outcome.closed_role_ids,
        completeness_score=outcome.profile.calculate_completeness(),
    )
