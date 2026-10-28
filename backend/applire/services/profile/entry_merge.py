# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 (U-11) — merge two entries of one section, and keep two
entries separate (U-9).

The split of judgement: exact duplicate bullets (whitespace / case normalised)
are a FACT and merge deterministically; "these two bullets say the same" is the
model's proposal (``decisions.find_duplicate_pairs``, ONE call per bullet list
that has bullets on both sides — ``responsibilities`` and ``achievements``;
``technologies`` are keyword atoms and only merge on exact equality); what to
KEEP of a pair is the candidate's (``choices.duplicates``). The write is one
adapter-only ``MergeEntries`` op through ``commit_ops`` with a pre-write
snapshot, so ``POST /api/profile/undo-last-merge`` restores both entries.
"""
from __future__ import annotations

import logging
import unicodedata
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.entry_merge import (
    AliasRow,
    DateConflict,
    DateSpan,
    DuplicateChoice,
    DuplicatePairOut,
    EntryMergePreview,
    EntryMergeRequest,
)
from applire.schemas.profile import MasterProfileData, MasterProfileResponse
from applire.services.profile.commit import CommitProvenance, SnapshotClass, commit_ops
from applire.services.profile.decisions import find_duplicate_pairs
from applire.services.profile.merge_verifier import verify_merge
from applire.services.profile.reconcile.apply import fill_empty_scalars, union_source_lists
from applire.services.profile.reconcile.ops import KeepEntriesSeparate, MergeEntries

logger = logging.getLogger(__name__)

#: lists carrying bullets, and the ones the model is asked about.
BULLET_LISTS = ("responsibilities", "achievements", "technologies")
MODEL_LISTS = ("responsibilities", "achievements")

#: section -> ((entry attr, alias list attr, alias-row key), ...)
_ALIAS_SPEC: dict[str, tuple[tuple[str, str | None, str], ...]] = {
    "work_experience": (("role", "role_aliases", "title"), ("company", "company_aliases", "company")),
    # a volunteer role / a project name have no alias list in the schema: shown
    # in the preview, stored nowhere (the receipt names the merged entry).
    "volunteer_activities": (("role", None, "title"), ("organization", "organization_aliases", "company")),
    "projects": (("name", None, "title"),),
    "education": (("degree", "degree_aliases", "title"), ("institution", "institution_aliases", "company")),
}


class EntryNotFound(LookupError):
    """404 — an unknown entry id."""


class SameEntry(ValueError):
    """422 — target and source are the same entry."""


class StalePreview(Exception):
    """409 ``stale_preview`` — the choices name text the entries no longer hold."""


class DatesChoiceRequired(Exception):
    """422 ``dates_choice_required``."""


class MergedTextRequired(Exception):
    """422 ``merged_text_required``."""


def _norm(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split()).casefold()


def _month(value: Any) -> str | None:
    return value.strip()[:7] if isinstance(value, str) and value.strip() else None


def _find(profile: MasterProfileData, section: str, entry_id: str):
    entry = next((e for e in getattr(profile, section) if e.id == entry_id), None)
    if entry is None:
        raise EntryNotFound(entry_id)
    return entry


def date_conflict(target: Any, source: Any) -> DateConflict | None:
    """Both entries state a start (or an end) and the months differ."""
    for name in ("start_date", "end_date"):
        a, b = _month(getattr(target, name, None)), _month(getattr(source, name, None))
        if a and b and a != b:
            return DateConflict(
                target=DateSpan(start_date=getattr(target, "start_date", None), end_date=getattr(target, "end_date", None)),
                source=DateSpan(start_date=getattr(source, "start_date", None), end_date=getattr(source, "end_date", None)),
            )
    return None


def _chosen_dates(target: Any, source: Any, side: str) -> dict[str, Any]:
    """The chosen side's dates; a date that side does not state is taken from
    the other (nothing stated is lost)."""
    first, second = (target, source) if side == "target" else (source, target)
    out: dict[str, Any] = {}
    for name in ("start_date", "end_date", "is_current"):
        if not hasattr(target, name):
            continue
        value = getattr(first, name)
        if value is None or value == "":
            value = getattr(second, name)
        out[name] = value
    return out


def alias_values(section: str, target: Any, source: Any) -> list[tuple[str | None, str, str]]:
    """``[(alias list attr | None, row key, value)]`` — B's differing value and
    B's own aliases, minus anything A already is or has."""
    out: list[tuple[str | None, str, str]] = []
    for attr, alias_attr, key in _ALIAS_SPEC[section]:
        have = {_norm(getattr(target, attr, ""))}
        if alias_attr:
            have |= {_norm(v) for v in getattr(target, alias_attr, [])}
        candidates = [getattr(source, attr, "")]
        if alias_attr:
            candidates += list(getattr(source, alias_attr, []))
        for value in candidates:
            if value and _norm(value) not in have:
                have.add(_norm(value))
                out.append((alias_attr, key, value))
    return out


def _bullets(entry: Any, name: str) -> list[str]:
    return list(getattr(entry, name, []) or [])


async def preview_merge(
    profile: MasterProfileData, section: str, target_id: str, source_id: str, provider
) -> EntryMergePreview:
    target, source = _find(profile, section, target_id), _find(profile, section, source_id)
    merged = target.model_copy(deep=True)
    fill_empty_scalars(merged, source)
    union_source_lists(merged, source, skip=BULLET_LISTS)  # ADV-5: shown before the click
    conflict = date_conflict(target, source)
    if conflict is not None:  # a conflict is the candidate's to decide, not filled
        for name in ("start_date", "end_date", "is_current"):
            if hasattr(merged, name):
                setattr(merged, name, getattr(target, name))

    aliases = [
        AliasRow(**{key: value}) for _attr, key, value in alias_values(section, target, source)
    ]

    pairs: list[DuplicatePairOut] = []
    unique: list[str] = []
    for name in BULLET_LISTS:
        t_list, s_list = _bullets(target, name), _bullets(source, name)
        if not s_list:
            continue
        t_norms = {_norm(b) for b in t_list}
        rest = [(i, b) for i, b in enumerate(s_list) if _norm(b) not in t_norms]
        in_pair: set[int] = set()
        if name in MODEL_LISTS and t_list and rest:
            found = await find_duplicate_pairs(provider, t_list, [b for _i, b in rest])
            for p in found:
                s_idx = rest[p.source_index][0]
                in_pair.add(s_idx)
                pairs.append(DuplicatePairOut(
                    pair_id=f"{name}:{p.target_index}:{s_idx}",
                    target_text=t_list[p.target_index],
                    source_text=s_list[s_idx],
                    proposal=p.proposal,
                ))
        seen: set[str] = set()
        for i, b in rest:
            if i not in in_pair and _norm(b) not in seen:
                seen.add(_norm(b))
                unique.append(b)

    return EntryMergePreview(
        merged_entry=merged.model_dump(mode="json"),
        aliases=aliases,
        date_conflict=conflict,
        duplicate_pairs=pairs,
        unique_bullets=unique,
    )


def _resolve_bullets(
    target: Any, source: Any, choices: list[DuplicateChoice]
) -> dict[str, list[str]]:
    """The final bullet lists. Raises :class:`StalePreview` when a choice does
    not echo text both entries still hold."""
    by_list: dict[str, list[DuplicateChoice]] = {}
    for c in choices:
        name = c.pair_id.split(":", 1)[0]
        if name not in BULLET_LISTS:
            raise StalePreview(c.pair_id)
        by_list.setdefault(name, []).append(c)

    out: dict[str, list[str]] = {}
    for name in BULLET_LISTS:
        t_list, s_list = _bullets(target, name), _bullets(source, name)
        result = list(t_list)
        orig_norms = [_norm(b) for b in t_list]
        s_norms = {_norm(b) for b in s_list}
        consumed: set[str] = set()  # normalised source bullets not to append
        for c in by_list.get(name, []):
            t_n, s_n = _norm(c.target_text), _norm(c.source_text)
            if t_n not in orig_norms or s_n not in s_norms:
                raise StalePreview(c.pair_id)
            idx = orig_norms.index(t_n)
            if c.outcome == "keep_current":
                consumed.add(s_n)
            elif c.outcome == "take_new":
                source_verbatim = next(b for b in s_list if _norm(b) == s_n)
                result[idx] = source_verbatim
                consumed.add(s_n)
            elif c.outcome == "merge":
                if not (c.merged_text and c.merged_text.strip()):
                    raise MergedTextRequired(c.pair_id)
                if not verify_merge(c.merged_text, [c.target_text, c.source_text]):
                    logger.info(
                        "entry merge: the candidate's merged sentence for %s is not verifiable "
                        "against its two sources; written as the candidate's own text", c.pair_id,
                    )
                result[idx] = c.merged_text.strip()
                consumed.add(s_n)
            # keep_both: the source bullet is appended below like any unique one
        have = {_norm(b) for b in result} | {_norm(b) for b in t_list}
        for b in s_list:
            n = _norm(b)
            if n in consumed or n in have:
                continue
            have.add(n)
            result.append(b)
        out[name] = result
    return out


def build_merge_op(
    profile: MasterProfileData, req: EntryMergeRequest
) -> MergeEntries:
    target = _find(profile, req.section, req.target_id)
    source = _find(profile, req.section, req.source_id)
    conflict = date_conflict(target, source)
    choices = req.choices
    dates = None
    if conflict is not None:
        if choices is None or choices.dates is None:
            raise DatesChoiceRequired()
        dates = _chosen_dates(target, source, choices.dates)
    bullets = _resolve_bullets(target, source, list(choices.duplicates) if choices else [])
    aliases: dict[str, list[str]] = {}
    for alias_attr, _key, value in alias_values(req.section, target, source):
        if alias_attr:
            aliases.setdefault(alias_attr, []).append(value)
    return MergeEntries(
        section=req.section, target_id=req.target_id, source_id=req.source_id,
        bullets=bullets if req.section != "education" else {}, aliases=aliases, dates=dates,
    )


async def entry_merge(
    db: AsyncSession, req: EntryMergeRequest, provider, *, user_id: uuid.UUID | None = None
) -> EntryMergePreview | MasterProfileResponse:
    from applire.services.profile import _get_latest, _to_response

    if req.target_id == req.source_id:
        raise SameEntry()
    record = await _get_latest(db, user_id)
    if record is None:
        raise LookupError("No profile found")
    profile = MasterProfileData.model_validate(record.profile_json)
    if req.mode == "preview":
        return await preview_merge(profile, req.section, req.target_id, req.source_id, provider)

    op = build_merge_op(profile, req)
    result = await commit_ops(
        db,
        [op],
        CommitProvenance(source="manual_edit", intake="entry_merge", actor="candidate"),
        record=record,
        grounding=None,
        snapshot=SnapshotClass.MERGE,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)


async def keep_entries_separate(
    db: AsyncSession, section: str, entry_ids: list[str], *, user_id: uuid.UUID | None = None
) -> MasterProfileResponse:
    from applire.services.profile import _get_latest, _to_response

    record = await _get_latest(db, user_id)
    if record is None:
        raise LookupError("No profile found")
    profile = MasterProfileData.model_validate(record.profile_json)
    if entry_ids[0] == entry_ids[1]:
        raise SameEntry()
    for entry_id in entry_ids:
        _find(profile, section, entry_id)
    result = await commit_ops(
        db,
        [KeepEntriesSeparate(section=section, entry_ids=entry_ids)],
        CommitProvenance(source="manual_edit", intake="entry_keep_separate", actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)
