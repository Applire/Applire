# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The interview receipt as a summary of ONE session (ADR-040 amended
2026-10-22, D-B3V1; founder ruling V-1 = A).

UX audit 2026-10-20 (Marcus): the interview completion listed every
``FieldChange`` of every interview-sourced trail record — 38 rows over ~7
screens, list values joined with ", " into ".," fragments, a role's
``technologies`` list shown as if it were a bullet, the same fact once per
section it was written to, and no session scope (a second application re-showed
the first interview's rows).

This module is the ONE producer of the user-facing receipt. It is a VIEW: the
decision trail (``metadata.enrichment_history``) stays complete — it is the
audit and undo record — and is never rewritten here.

Everything below is a FACT about the write (ADR-062 clause 1): which section and
field an item went to, whether the same normalised text was written twice, which
entity holds it. Nothing judges whether two different sentences say the same
thing — that is the reconciler's job (ADR-082), not a receipt's.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from applire.schemas.profile import (
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ReceiptEntityCount,
    ReceiptFact,
    ReceiptGroup,
    ReceiptSummary,
)
from applire.services.profile.entity_label import label_for
from applire.services.profile.reconcile.apply import _norm

_INTERVIEW_SOURCES = frozenset({"interview", "agent_interview"})

#: The fixed kind order "Das Wichtigste" reads in (a fact: the kind of entry;
#: not a judgement of importance between two entries of one kind).
_TOP_KINDS: tuple[str, ...] = (
    "story", "result", "position", "project", "certification", "responsibility",
)

#: Group order on the summary.
_SECTION_ORDER: tuple[str, ...] = (
    "work_experience", "projects", "volunteer_activities", "signature_stories",
    "skills", "certifications", "education", "languages", "publications",
    "professional_summary", "personal_info", "limits",
)

_ENGAGEMENT_SECTIONS = frozenset({"work_experience", "projects", "volunteer_activities"})
_BULLET_KIND = {"responsibilities": "responsibility", "achievements": "result", "technologies": "tool"}
_FLAT_KIND = {
    "skills": "skill", "certifications": "certification", "education": "education",
    "languages": "language", "publications": "detail",
}

_TOP_LIMIT = 5
_PREVIEW_LIMIT = 5


def _session_records(
    profile: MasterProfileData, session_id: str | None
) -> tuple[str | None, list[EnrichmentRecord]]:
    trail = profile.metadata.enrichment_history if profile.metadata else []
    interview = [r for r in trail if r.source in _INTERVIEW_SOURCES]
    if session_id is None:
        session_id = next(
            (r.source_session_id for r in reversed(interview) if r.source_session_id), None
        )
        if session_id is None:
            return None, []
    return session_id, [r for r in interview if r.source_session_id == session_id]


def _items(value: Any) -> list[str]:
    """A list value is one item per element; a scalar is one item."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return [text] if text else []
    return []


def _locate(profile: MasterProfileData, section: str, field: str, text: str) -> str | None:
    """The entity in ``section`` whose ``field`` list holds ``text`` — a lookup,
    for trail records written before ``FieldChange.entity_id`` existed."""
    key = _norm(text)
    for entity in getattr(profile, section, None) or []:
        values = getattr(entity, field, None)
        if isinstance(values, list) and any(_norm(v) == key for v in values if isinstance(v, str)):
            return getattr(entity, "id", None)
    return None


def _raw_facts(change: FieldChange, profile: MasterProfileData) -> Iterable[ReceiptFact]:
    section, field = change.section, change.field
    if section == "metadata":
        if field == "denied_concepts":
            for text in _items(change.new_value):
                yield ReceiptFact(key="", section="limits", kind="limit", action=change.action, text=text)
        return  # every other metadata change is bookkeeping, not a fact
    if section == "signature_stories":
        story = change.new_value if isinstance(change.new_value, dict) else {}
        title = str(story.get("title") or field or "").strip()
        if title:
            yield ReceiptFact(
                key="", section=section, kind="story", action=change.action, text=title,
                detail=(str(story.get("outcome")).strip() or None) if story.get("outcome") else None,
            )
        return
    if section in _ENGAGEMENT_SECTIONS:
        if field in _BULLET_KIND:
            for text in _items(change.new_value):
                entity_id = change.entity_id or _locate(profile, section, field, text)
                yield ReceiptFact(
                    key="", section=section, kind=_BULLET_KIND[field], action=change.action,
                    text=text, entity_label=label_for(profile, entity_id),
                )
            return
        label = label_for(profile, change.entity_id)
        if change.action == "added" and field in {"company", "name", "organization"}:
            kind = "project" if section == "projects" else "position"
            for text in _items(change.new_value):
                yield ReceiptFact(
                    key="", section=section, kind=kind, action=change.action,
                    text=label or text, entity_label=label,
                )
            return
        for text in _items(change.new_value):
            yield ReceiptFact(
                key="", section=section, kind="detail", action=change.action,
                text=text, entity_label=label,
            )
        return
    if section in _FLAT_KIND:
        if field == "aliases":
            return  # an alternate name for an entry already there — bookkeeping
        for text in _items(change.new_value):
            yield ReceiptFact(key="", section=section, kind=_FLAT_KIND[section], action=change.action, text=text)
        return
    for text in _items(change.new_value):
        yield ReceiptFact(key="", section=section, kind="detail", action=change.action, text=text)


def summarize_session_receipts(
    profile: MasterProfileData, session_id: str | None
) -> ReceiptSummary:
    """The receipt summary of one interview session (latest when ``None``)."""
    sid, records = _session_records(profile, session_id)
    facts: list[ReceiptFact] = []
    by_key: dict[str, ReceiptFact] = {}
    for record in records:
        for change in record.changes:
            for fact in _raw_facts(change, profile):
                key = _norm(fact.text)
                if not key:
                    continue
                if fact.kind == "limit":
                    key = f"limit:{key}"
                seen = by_key.get(key)
                if seen is not None:
                    if seen.kind == "tool" and fact.kind == "skill":
                        # One fact, listed where the vault keeps capabilities:
                        # under Skills, "also at <role>" (the technologies row).
                        seen.also_in = [seen.section, *seen.also_in]
                        seen.section, seen.kind = fact.section, fact.kind
                    elif fact.section != seen.section and fact.section not in seen.also_in:
                        seen.also_in.append(fact.section)
                    if seen.entity_label is None and fact.entity_label:
                        seen.entity_label = fact.entity_label
                    continue
                fact.key = key
                by_key[key] = fact
                facts.append(fact)

    order = {s: i for i, s in enumerate(_SECTION_ORDER)}
    facts.sort(key=lambda f: order.get(f.section, len(order)))  # stable: trail order within

    groups: list[ReceiptGroup] = []
    for section in dict.fromkeys(f.section for f in facts):
        members = [f for f in facts if f.section == section]
        entities = Counter(f.entity_label for f in members if f.entity_label)
        preview = [f.text for f in members[:_PREVIEW_LIMIT]]
        groups.append(ReceiptGroup(
            section=section,
            count=len(members),
            entities=[ReceiptEntityCount(label=label, count=n) for label, n in entities.items()],
            preview=preview,
            more=len(members) - len(preview),
        ))

    rank = {k: i for i, k in enumerate(_TOP_KINDS)}
    top = sorted((f for f in facts if f.kind in rank), key=lambda f: rank[f.kind])[:_TOP_LIMIT]

    return ReceiptSummary(
        session_id=sid,
        answers=len(records),
        facts_total=len(facts),
        groups=groups,
        top=top,
        facts=facts,
    )
