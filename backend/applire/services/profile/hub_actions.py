# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UAT-3d (Hazelnut build 5) — the profile-health hub's one-action resolvers
that write: the unit input and "Übernehmen" for a not-applied import item.

Both are direct acts of the candidate through the committer
(``source="manual_edit"``, no grounding, no snapshot), like ``separate_match``.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.profile import MasterProfileData, MasterProfileResponse
from applire.utils.budget_unit import budget_needs_unit

UNIT_INTAKE = "health_unit"
ADOPT_INTAKE = "not_applied_adopt"


class HubEntryNotFound(LookupError):
    """The named work entry / trail record / item is not on the profile (404)."""


class UnitNotApplicable(ValueError):
    """The entry has no budget value, or the value already carries a unit (422)."""


class NotAppliedConflict(Exception):
    """The item is already adopted (409)."""


class NotAppliedUnprocessable(Exception):
    """The item captured no entry to adopt (422)."""


async def set_budget_unit(
    db: AsyncSession, *, entry_id: str, unit: str, user_id: uuid.UUID | None = None
) -> MasterProfileResponse:
    """Write ``"<stored value> <unit>"`` onto one work entry's ``budget_managed``.

    The narrowest existing op that edits one entry field: ``ReplaceSection`` on
    ``work_experience`` (the section door's act — ``SetField`` is fill-only and
    ``ResolveField`` needs a dispute), so the committer diffs it to ONE
    per-field receipt on that entry.
    """
    from applire.services.profile import _get_latest, _to_response
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import ReplaceSection

    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    profile = MasterProfileData.model_validate(record.profile_json)
    entry = next((e for e in profile.work_experience if e.id == entry_id), None)
    if entry is None:
        raise HubEntryNotFound(entry_id)
    if not budget_needs_unit(entry.budget_managed):
        raise UnitNotApplicable("no_budget_value_or_unit_present")
    section = profile.model_dump(mode="json")["work_experience"]
    for row in section:
        if row.get("id") == entry_id:
            row["budget_managed"] = f"{str(entry.budget_managed).strip()} {unit}"
    result = await commit_ops(
        db,
        [ReplaceSection(section="work_experience", value=section)],
        CommitProvenance(source="manual_edit", intake=UNIT_INTAKE, actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)


async def adopt_not_applied_item(
    db: AsyncSession, *, issue_id: str, item_key: str, user_id: uuid.UUID | None = None
) -> MasterProfileResponse:
    """"Übernehmen": append one not-applied item's captured entry to its section
    and stamp the receipt item ``adopted_at`` (one ``AdoptNotApplied`` op)."""
    from applire.services.profile import _get_latest, _to_response
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.apply import (
        NotAppliedNotAdoptableError,
        find_adoptable_item,
    )
    from applire.services.profile.reconcile.ops import AdoptNotApplied

    prefix = "not_applied:"
    if not issue_id.startswith(prefix) or len(issue_id) == len(prefix):
        raise HubEntryNotFound(issue_id)
    record_id = issue_id[len(prefix):]
    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    try:
        find_adoptable_item(
            MasterProfileData.model_validate(record.profile_json), record_id, item_key
        )
        result = await commit_ops(
            db,
            [AdoptNotApplied(record_id=record_id, item_key=item_key)],
            CommitProvenance(source="manual_edit", intake=ADOPT_INTAKE, actor="candidate"),
            record=record,
            grounding=None,
            snapshot=None,
            llm_provider=None,
        )
    except NotAppliedNotAdoptableError as exc:
        if exc.code == "unknown":
            raise HubEntryNotFound(issue_id) from exc
        if exc.code == "already_adopted":
            raise NotAppliedConflict(exc.code) from exc
        raise NotAppliedUnprocessable(exc.code) from exc
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)
