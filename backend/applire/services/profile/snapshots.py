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
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire import constants
from applire.models.profile import MasterProfile, ProfileSnapshot
from applire.services.profile.commit import _as_utc_instant


class UndoStale(Exception):
    """ADV-8 — the caller named the merge it means to undo (the profile's
    ``updated_at`` right after it) and the profile has moved since, or the
    newest snapshot is not that merge. Nothing is restored."""


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


#: How an undo reads each change it takes back.
_REVERSED_ACTION = {"added": "removed", "removed": "added", "updated": "updated", "merged": "updated"}


def _trail(profile_json: dict | None) -> list:
    metadata = (profile_json or {}).get("metadata")
    history = metadata.get("enrichment_history") if isinstance(metadata, dict) else None
    return history if isinstance(history, list) else []


def _with_undo_record(restored: dict, current: dict | None) -> dict:
    """The restored profile, with ONE dated ``undo`` record on its trail
    (Hazelnut b7 adversarial A-8, main MD7-10).

    The restore puts the pre-merge trail back, so without it the newest record
    would predate every document written from the merged content — the "Profil
    aktualisiert" strip and the dossier's re-tailor nudge
    (``services/profile_freshness``) would say nothing while the document still
    carries the undone facts. The record lists every change the undo takes back
    — the changes of each trail record the restore drops, reversed
    (added → removed, removed → added, otherwise updated; old/new swapped) — so
    the "what changed & why" surface reads it like any other record, and a
    document created after the undone merge and before the undo reads as
    behind. ``reverts_since`` (the earliest dropped record's timestamp) keeps a
    document OLDER than the merge quiet: it never held the undone content, and
    the profile is back to what it was written from. Always appended, even with
    no change: the trail is unconditional (ADR-063 invariant 3).
    """
    from applire.schemas.profile import EnrichmentRecord, FieldChange

    from applire.services.profile_freshness import parse_trail_timestamp

    kept = {r.get("id") for r in _trail(restored) if isinstance(r, dict)}
    changes: list[FieldChange] = []
    taken_back: list[datetime | None] = []
    for record in reversed(_trail(current)):
        if not isinstance(record, dict) or record.get("id") in kept:
            continue
        taken_back.append(parse_trail_timestamp(record.get("timestamp")))
        for change in reversed(record.get("changes") or []):
            if not isinstance(change, dict):
                continue
            action = _REVERSED_ACTION.get(change.get("action"))
            section, field = change.get("section"), change.get("field")
            if action is None or not isinstance(section, str) or not isinstance(field, str):
                continue
            changes.append(
                FieldChange(
                    section=section,
                    field=field,
                    action=action,
                    old_value=change.get("new_value"),
                    new_value=change.get("old_value"),
                    entity_id=change.get("entity_id"),
                )
            )
    # A document older than everything taken back never held it; an unreadable
    # timestamp leaves the bound off (the cue then shows — the safe direction).
    reverts_since = min(taken_back) if taken_back and None not in taken_back else None
    undo = EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="undo",
        reverts_since=reverts_since,
        changes=changes,
    )

    out = dict(restored)
    metadata = dict(out.get("metadata") or {}) if isinstance(out.get("metadata"), dict) else {}
    metadata["enrichment_history"] = [*_trail(restored), undo.model_dump(mode="json")]
    out["metadata"] = metadata
    return out


async def undo_last_merge(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    expected_updated_at: datetime | None = None,
) -> UndoResult:
    """Restore the most recent pre-merge snapshot.

    Clears the conflicts the undone merge introduced (the restored pre-merge JSON
    carries the pre-merge conflict set), and warns when later edits are discarded
    — i.e. the current profile head is no longer the merge this snapshot preceded.
    Idempotent: after a successful undo all snapshots are consumed, so a repeat
    call is a no-op (single-level "undo last merge"; multi-level history deferred).
    The restored trail ends in one dated ``undo`` record (A-8,
    :func:`_with_undo_record`), so documents written from the undone content
    read as behind the profile.
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
    # ADV-8 — with a basis, refuse instead of discarding: an Undo click means
    # "the merge I just made", never "whatever the newest snapshot is".
    # Instants are compared on the PERSISTED value (SQLite hands back naive
    # datetimes for a timezone=True column), as the lost-update check does.
    if expected_updated_at is not None and (
        discarded_later_edits
        or _as_utc_instant(profile.updated_at) != _as_utc_instant(expected_updated_at)
    ):
        raise UndoStale()

    profile.profile_json = _with_undo_record(
        _keep_user_managed_fields(snapshot.profile_json, profile.profile_json),
        profile.profile_json,
    )
    # Consume the whole snapshot chain so a retry is a no-op (idempotent) and no
    # accidental multi-level peel-back occurs (MVP = undo the last merge only).
    await db.execute(
        delete(ProfileSnapshot).where(ProfileSnapshot.profile_id == profile.id)
    )
    await db.commit()
    return UndoResult(restored=True, discarded_later_edits=discarded_later_edits)
