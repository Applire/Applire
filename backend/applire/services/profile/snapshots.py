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

"""
US168 (E033 / ADR-042) — Master Profile snapshot + undo last merge.

``capture_pre_merge_snapshot`` stores the current ``profile_json`` before a merge
overwrites it (unconditional capture; bounded per profile). ``undo_last_merge``
restores the most recent snapshot to recover from an accidental bad merge.

Architecture boundary (ADR-042 / ADR-040): snapshots are profile-derived PII,
cascade-deleted with the profile under existing erasure — no new retention
surface, no source-file dependency. Coarse whole-profile restore (per-field
revert deferred).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire import constants
from applire.models.profile import MasterProfile, ProfileSnapshot


@dataclass
class UndoResult:
    restored: bool
    discarded_later_edits: bool


async def capture_pre_merge_snapshot(
    db: AsyncSession,
    *,
    profile_id: uuid.UUID,
    profile_json: dict,
    enrichment_record_id: str,
) -> ProfileSnapshot:
    """Snapshot the pre-merge ``profile_json``, keyed to the merge's enrichment
    record. Added to the session (caller commits within the merge transaction so
    snapshot and merge are atomic); prunes to ``SNAPSHOT_MAX_PER_PROFILE``.
    """
    snapshot = ProfileSnapshot(
        profile_id=profile_id,
        enrichment_record_id=str(enrichment_record_id),
        profile_json=profile_json,
    )
    db.add(snapshot)
    await db.flush()
    await _prune(db, profile_id)
    return snapshot


async def _prune(db: AsyncSession, profile_id: uuid.UUID) -> None:
    """Keep only the most-recent ``SNAPSHOT_MAX_PER_PROFILE`` snapshots."""
    keep = constants.SNAPSHOT_MAX_PER_PROFILE
    rows = (
        await db.execute(
            select(ProfileSnapshot.id)
            .where(ProfileSnapshot.profile_id == profile_id)
            .order_by(ProfileSnapshot.created_at.desc(), ProfileSnapshot.id.desc())
        )
    ).scalars().all()
    stale = rows[keep:]
    if stale:
        await db.execute(delete(ProfileSnapshot).where(ProfileSnapshot.id.in_(stale)))


async def _latest_profile(db: AsyncSession, user_id: uuid.UUID | None = None) -> MasterProfile | None:
    """The owner's live profile (ADR-092 cl. 2) — the one read path."""
    from applire.services.profile import get_profile_for_user

    return await get_profile_for_user(db, user_id)


def _head_enrichment_id(profile_json: dict) -> str | None:
    history = (profile_json.get("metadata") or {}).get("enrichment_history") or []
    return history[-1].get("id") if history else None


def _keep_user_managed_fields(restored: dict | None, current: dict | None) -> dict:
    """The snapshot, with the CURRENT value of every upload-owned field (MD-30).

    ``personal_info.photo_url`` belongs to the photo endpoints, not to a merge:
    an undo that rolled it back would point the vault at the file a later photo
    upload already deleted (the render then drops the photo) and orphan the new
    file for the retention scan to reclaim. An undo reverts what the MERGE
    wrote; the photo was never the merge's to write (``apply_ops`` keeps it).
    """
    from applire.schemas.profile import USER_MANAGED_PERSONAL_INFO_FIELDS

    out = dict(restored or {})
    current_pi = (current or {}).get("personal_info") or {}
    pi = dict(out.get("personal_info") or {})
    changed = False
    for field_name in USER_MANAGED_PERSONAL_INFO_FIELDS:
        keep = current_pi.get(field_name)
        if (pi.get(field_name) or None) == (keep or None):
            continue  # already equal — the snapshot is restored byte-for-byte
        if keep is None:
            pi.pop(field_name, None)
        else:
            pi[field_name] = keep
        changed = True
    if changed:
        out["personal_info"] = pi
    return out


async def undo_last_merge(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> UndoResult:
    """Restore the most recent pre-merge snapshot.

    Clears the conflicts the undone merge introduced (the restored pre-merge JSON
    carries the pre-merge conflict set), and warns when later edits are discarded
    — i.e. the current profile head is no longer the merge this snapshot preceded.
    Idempotent: after a successful undo all snapshots are consumed, so a repeat
    call is a no-op (single-level "undo last merge"; multi-level history deferred).
    """
    profile = await _latest_profile(db, user_id)
    if profile is None:
        return UndoResult(restored=False, discarded_later_edits=False)

    snapshot = (
        await db.execute(
            select(ProfileSnapshot)
            .where(ProfileSnapshot.profile_id == profile.id)
            .order_by(ProfileSnapshot.created_at.desc(), ProfileSnapshot.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snapshot is None:
        return UndoResult(restored=False, discarded_later_edits=False)

    discarded_later_edits = (
        _head_enrichment_id(profile.profile_json) != snapshot.enrichment_record_id
    )

    profile.profile_json = _keep_user_managed_fields(snapshot.profile_json, profile.profile_json)
    # Consume the whole snapshot chain so a retry is a no-op (idempotent) and no
    # accidental multi-level peel-back occurs (MVP = undo the last merge only).
    await db.execute(
        delete(ProfileSnapshot).where(ProfileSnapshot.profile_id == profile.id)
    )
    await db.commit()
    return UndoResult(restored=True, discarded_later_edits=discarded_later_edits)
