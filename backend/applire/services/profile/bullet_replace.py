# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Q3-4 (Sorbet quality wave 2b; founder rulings Q3-4 and Q8-3 = A + dismiss) —
an interview answer that only ADDS detail to a held bullet replaces it, with an
undo.

**Who decides.** The restatement check's model (one call that already runs on
the interview door, ADR-046 D-17) says, per NEW bullet, which held bullet ALL of
whose facts it states (``covers``) and which facts of that held bullet it does
not state (``dropped``). Code only acts on that explicit statement, and only on
the safe side (``restatement.replacement_ops``): every figure and every
name-like token of the old bullet must reappear in the new one. Any doubt keeps
both bullets — the behaviour before Q3-4.

**What this module does.** The committer's three branches (``apply_ops``):

* :func:`apply_replace` — put the new text in the old bullet's place (same field,
  same index) and record ``replaced_bullets`` on the entry; when the old bullet
  is gone by apply time, append the new text instead (a fact is never lost);
* :func:`apply_undo` — "Rückgängig": the old bullet goes back BESIDE the new one
  (right after it, same field) and the record is removed;
* :func:`apply_dismiss` — "Ausblenden": the record is removed.

and the two class rules every write passes: :func:`keep_replaced_bullets` (the
section door carries the stored records — a payload can neither forge nor wipe
one) and :func:`prune_replaced_bullets` (a record whose new bullet is no longer
a bullet of its entry ends: an edit or a delete of the new bullet ends the line).
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.profile import FieldChange, MasterProfileData, ReplacedBullet

RATIONALE_REPLACED = "bullet_replaced"
RATIONALE_UNDONE = "bullet_replace_undone"
ANSWER_INTAKE = "bullet_replace"
_FIELDS = ("responsibilities", "achievements")


class ReplaceRecordNotFound(LookupError):
    """No ``replaced_bullets`` record with that text on that entry."""


def _entry(profile: MasterProfileData, entry_id: str) -> Any:
    for e in profile.work_experience:
        if str(e.id) == str(entry_id):
            return e
    return None


def _label(entry: Any) -> str:
    return entry.company or entry.role or entry.id


def apply_replace(op: Any, profile: MasterProfileData, changes: list[FieldChange]) -> None:
    entry = _entry(profile, op.entry_id)
    if entry is None:
        return
    bullets = list(getattr(entry, op.field) or [])
    try:
        index = bullets.index(op.old)
    except ValueError:
        index = -1
    if index < 0:
        if getattr(op, "absorbed", None):
            # WP-V: the held bullet is gone; the turn's own bullet still states
            # the answer — a merged text would bring the removed bullet back.
            return
        # The held bullet is gone (edited or removed meanwhile): the new text
        # is a new bullet — the answer's fact is never lost.
        if op.new not in bullets:
            bullets.append(op.new)
            setattr(entry, op.field, bullets)
            changes.append(FieldChange(
                section="work_experience", field=op.field, action="added",
                new_value=op.new, entity_id=entry.id,
                rationale=f"Added to {_label(entry)!r} from the candidate's answer.",
            ))
        return
    bullets[index] = op.new
    setattr(entry, op.field, bullets)
    absorbed = getattr(op, "absorbed", None)
    if absorbed:
        _remove_absorbed(entry, op.field, index, absorbed, changes)
    entry.replaced_bullets = [r for r in entry.replaced_bullets if r.text != op.new] + [
        ReplacedBullet(text=op.new, replaced=op.old, field=op.field, source=op.source)
    ]
    changes.append(FieldChange(
        section="work_experience", field=op.field, action="updated",
        old_value=op.old, new_value=op.new, entity_id=entry.id,
        rationale=(
            f"Replaced on {_label(entry)!r}: the candidate's answer states this bullet "
            "with more detail; the old wording can be put back."
        ),
        rationale_key=RATIONALE_REPLACED,
    ))


def _remove_absorbed(
    entry: Any, field: str, kept_index: int, text: str, changes: list[FieldChange]
) -> None:
    """Remove the first copy of ``text`` from the entry's bullet fields, never the
    slot the replace just wrote (WP-V: the turn's bullet the merged one states)."""
    for f in ("responsibilities", "achievements"):
        bullets = list(getattr(entry, f) or [])
        for i, b in enumerate(bullets):
            if b == text and not (f == field and i == kept_index):
                del bullets[i]
                setattr(entry, f, bullets)
                changes.append(FieldChange(
                    section="work_experience", field=f, action="removed",
                    old_value=text, entity_id=entry.id,
                    rationale=(
                        f"Merged into the replaced bullet on {_label(entry)!r} "
                        "at the candidate's confirmation."
                    ),
                ))
                return


def _record(entry: Any, text: str) -> ReplacedBullet:
    for r in entry.replaced_bullets if entry is not None else []:
        if r.text == text:
            return r
    # Suspected S1 (adv-2b) — never the bullet text: an unhandled raise would
    # carry it into a log.
    raise ReplaceRecordNotFound("no replaced-bullet record with that text on the entry")


def apply_undo(op: Any, profile: MasterProfileData, changes: list[FieldChange]) -> None:
    entry = _entry(profile, op.entry_id)
    record = _record(entry, op.text)
    bullets = list(getattr(entry, record.field) or [])
    if record.replaced not in bullets:
        at = bullets.index(record.text) + 1 if record.text in bullets else len(bullets)
        bullets.insert(at, record.replaced)
        setattr(entry, record.field, bullets)
        changes.append(FieldChange(
            section="work_experience", field=record.field, action="added",
            new_value=record.replaced, entity_id=entry.id,
            rationale=f"Put back on {_label(entry)!r} beside the bullet that had replaced it.",
            rationale_key=RATIONALE_UNDONE,
        ))
    entry.replaced_bullets = [r for r in entry.replaced_bullets if r is not record]


def apply_dismiss(op: Any, profile: MasterProfileData, changes: list[FieldChange]) -> None:
    entry = _entry(profile, op.entry_id)
    record = _record(entry, op.text)
    entry.replaced_bullets = [r for r in entry.replaced_bullets if r is not record]


def restore_replaced_bullets(
    before: MasterProfileData, after: MasterProfileData, changes: list[FieldChange], touched: set[str],
    merged: list[tuple[str, str]] | None = None,
) -> list[FieldChange]:
    """Class guard: an entry no ``ReplaceBullet`` / undo / dismiss op of this
    batch touched keeps the records it held before the batch (none for a new
    entry); a receipt another op wrote on the field is dropped. The target of
    an entry merge (``merged``: the batch's ``(target, source)`` merges in
    order, a chain replayed) also takes the source's records — its bullets
    moved with them; the committer's prune then ends any record whose bullet
    the merge did not keep."""
    held = {str(e.id): [r.model_copy() for r in e.replaced_bullets] for e in before.work_experience}
    for target, source in merged or []:
        into = held.setdefault(str(target), [])
        texts = {r.text for r in into}
        for r in held.pop(str(source), []):
            if r.text not in texts:
                into.append(r)
                texts.add(r.text)
    restored: set[str] = set()
    for entry in after.work_experience:
        eid = str(entry.id)
        if eid in touched:
            continue
        want = held.get(eid, [])
        if [r.model_dump() for r in entry.replaced_bullets] != [r.model_dump() for r in want]:
            entry.replaced_bullets = want
            restored.add(eid)
    if not restored:
        return changes
    return [
        c for c in changes
        if not (c.field == "replaced_bullets" and str(c.entity_id or "") in restored)
        and not (c.field == "replaced_bullets" and c.entity_id is None)
    ]


def prune_replaced_bullets(profile: MasterProfileData) -> None:
    """A record whose new bullet is no longer a bullet of its entry ends."""
    for entry in profile.work_experience:
        if entry.replaced_bullets:
            entry.replaced_bullets = [
                r for r in entry.replaced_bullets if r.text in (getattr(entry, r.field) or [])
            ]


def keep_replaced_bullets(section: str, before: Any, after: Any) -> Any:
    """The section door (PATCH, MCP ``update_profile``): each entry keeps the
    records the entry with that id held, whatever the payload says; a new entry
    carries none. Same shape as ``apply._keep_leadership_kind``."""
    if section != "work_experience" or not isinstance(after, list):
        return after
    held = {
        str(e["id"]): e.get("replaced_bullets")
        for e in before or [] if isinstance(e, dict) and e.get("id")
    }
    result = []
    for entry in after:
        if isinstance(entry, dict):
            entry = dict(entry)
            kept = held.get(str(entry.get("id") or ""))
            if kept:
                entry["replaced_bullets"] = kept
            else:
                entry.pop("replaced_bullets", None)
        result.append(entry)
    return result


async def _commit_one(db: AsyncSession, op: Any, user_id: uuid.UUID | None):
    from applire.services.profile import _get_latest, _to_response
    from applire.services.profile.commit import CommitProvenance, commit_ops

    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    _record(_entry(MasterProfileData.model_validate(record.profile_json), op.entry_id), op.text)
    result = await commit_ops(
        db,
        [op],
        CommitProvenance(source="manual_edit", intake=ANSWER_INTAKE, actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)


async def undo_bullet_replace(
    db: AsyncSession, *, entry_id: str, text: str, user_id: uuid.UUID | None = None,
):
    """"Rückgängig" — one ``UndoBulletReplace`` op through the committer."""
    from applire.services.profile.reconcile.ops import UndoBulletReplace

    return await _commit_one(db, UndoBulletReplace(entry_id=entry_id, text=text), user_id)


async def dismiss_bullet_replace(
    db: AsyncSession, *, entry_id: str, text: str, user_id: uuid.UUID | None = None,
):
    """"Ausblenden" — one ``DismissBulletReplace`` op through the committer."""
    from applire.services.profile.reconcile.ops import DismissBulletReplace

    return await _commit_one(db, DismissBulletReplace(entry_id=entry_id, text=text), user_id)
