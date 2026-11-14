# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-Q5 (Sorbet quality-1) — a role's leadership KIND is a stored fact.

``WorkEntry.leadership_kind`` ("disciplinary" | "functional" | None) is written
ONLY from the person's answer to the dedicated leadership-kind question, through
the one op ``RecordLeadershipKind`` and the committer (vault write matrix,
arc42 §5.3.19a). No prose reader: the bullet text never sets the kind
(the adversarial re-check N1–N3 showed every reading of the prose either
promoted a mention to a fact or demoted a true form).

The fixed options (RULINGS MD-Q5):

* "Disziplinarisch (Personalverantwortung)" / "Line management" → ``disciplinary``
* "Beides" / "Both" → ``disciplinary`` (the line names the stronger, stated kind)
* "Fachliche Anleitung" / "Functional guidance" → ``functional``
* own words that are not one of the options, or a skipped question → no kind
  (the neutral line), but the role counts as asked and is not asked again.

Callers (the interview's kind ask, Q2):

* :func:`option_for_answer` maps the answer text to an option (exact option
  label only — an own-words answer is never interpreted);
* :func:`leadership_kind_op` builds the op for a ``commit_ops`` batch the caller
  already commits (one turn, one ``EnrichmentRecord``);
* :func:`record_leadership_kind` commits it on its own (the candidate's act:
  source ``interview`` / ``agent_interview``, intake ``leadership_kind_answer``).
"""

from __future__ import annotations

import unicodedata
import uuid
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.profile import FieldChange, LeadershipKindRecord, MasterProfileData

Option = Literal["disciplinary", "functional", "both", "own_words", "skipped"]

#: The intake name on the ``EnrichmentRecord`` and the receipt's rationale key.
ANSWER_INTAKE = "leadership_kind_answer"
RATIONALE_RECORDED = "leadership_kind_recorded"
RATIONALE_UNKNOWN = "leadership_kind_left_open"

#: option → the stored kind. ``both`` stores disciplinary (RULINGS MD-Q5).
KIND_OF_OPTION: dict[str, str | None] = {
    "disciplinary": "disciplinary",
    "both": "disciplinary",
    "functional": "functional",
    "own_words": None,
    "skipped": None,
}

#: The fixed option labels the question offers, per language, in order.
OPTION_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "disciplinary": "Disziplinarisch (Personalverantwortung)",
        "functional": "Fachliche Anleitung",
        "both": "Beides",
    },
    "en": {
        "disciplinary": "Disciplinary (line management)",
        "functional": "Functional guidance",
        "both": "Both",
    },
}

_SOURCES = ("interview", "agent_interview", "testimony")


class LeadershipKindEntryNotFound(LookupError):
    """No single work entry carries the given id (or company + role)."""


def _norm(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split()).casefold()


def option_for_answer(text: str | None) -> Option:
    """The option an answer names: an EXACT option label (either language,
    case and spacing ignored) or the option key itself; empty → ``skipped``;
    anything else → ``own_words`` (never interpreted — the neutral line)."""
    t = _norm(text)
    if not t:
        return "skipped"
    for labels in OPTION_LABELS.values():
        for option, label in labels.items():
            if t == _norm(label):
                return option  # type: ignore[return-value]
    if t in KIND_OF_OPTION:
        return t  # type: ignore[return-value]
    return "own_words"


def leadership_kind_op(
    entry_id: str = "",
    option: Option = "skipped",
    *,
    answer: str = "",
    company: str = "",
    role: str = "",
    source: str = "interview",
):
    """The ``RecordLeadershipKind`` op for one role — for a caller that commits
    it in its own ``commit_ops`` batch."""
    from applire.services.profile.reconcile.ops import RecordLeadershipKind

    return RecordLeadershipKind(
        entry_id=str(entry_id or ""), company=company or "", role=role or "",
        option=option, answer=answer or "", source=source,
    )


def find_entry(profile: MasterProfileData, entry_id: str = "", company: str = "", role: str = ""):
    """The one work entry the op addresses: by id; else, for an entry stored
    without one, the single entry with that company and role."""
    if entry_id:
        hit = next((e for e in profile.work_experience if e.id == entry_id), None)
        if hit is not None:
            return hit
    if company or role:
        hits = [
            e for e in profile.work_experience
            if _norm(e.company) == _norm(company) and _norm(e.role) == _norm(role)
        ]
        if len(hits) == 1:
            return hits[0]
    raise LeadershipKindEntryNotFound(entry_id or f"{company}|{role}")


def apply_leadership_kind_op(op, profile: MasterProfileData, changes: list):
    """The applier branch (called from ``reconcile.apply.apply_ops``): set the
    kind and the record on the one entry, receipt it, and return the entry (the
    committer's class guard lets exactly these entries keep a changed kind)."""
    entry = find_entry(profile, op.entry_id, op.company, op.role)
    kind = KIND_OF_OPTION[op.option]
    before = entry.leadership_kind
    entry.leadership_kind = kind  # type: ignore[assignment]
    entry.leadership_kind_record = LeadershipKindRecord(
        option=op.option, answer=" ".join((op.answer or "").split()), source=op.source,
    )
    label = entry.company or entry.role or entry.id
    changes.append(FieldChange(
        section="work_experience", field="leadership_kind",
        action="updated" if before else "added",
        old_value=before, new_value=kind,
        rationale=(
            f"Recorded on {label!r}: the candidate said the team size is {kind} leadership."
            if kind else
            f"Left open on {label!r}: the candidate's answer named no kind of leadership; "
            "the CV states the team size only."
        ),
        rationale_key=RATIONALE_RECORDED if kind else RATIONALE_UNKNOWN,
        entity_id=entry.id,
    ))
    return entry


async def record_leadership_kind(
    db: AsyncSession,
    *,
    entry_id: str = "",
    option: Option,
    answer: str = "",
    company: str = "",
    role: str = "",
    source: str = "interview",
    user_id: uuid.UUID | None = None,
):
    """Commit one role's answer on its own (one ``EnrichmentRecord``, intake
    ``leadership_kind_answer``, actor ``candidate``; no grounding — the option
    is the candidate's pick, never model output). Returns the ``CommitResult``.
    Raises :class:`LeadershipKindEntryNotFound` / ``LookupError``."""
    from applire.services.profile import _get_latest
    from applire.services.profile.commit import CommitProvenance, commit_ops

    if source not in _SOURCES:
        raise ValueError(f"record_leadership_kind: unknown source {source!r}")
    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    find_entry(MasterProfileData.model_validate(record.profile_json), entry_id, company, role)
    result = await commit_ops(
        db,
        [leadership_kind_op(entry_id, option, answer=answer, company=company, role=role, source=source)],
        CommitProvenance(source=source, intake=ANSWER_INTAKE, actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return result
