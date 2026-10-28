# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING B-1 (Hazelnut build 5, U-7) — "Ist das erreicht?" for a bullet of a
CURRENT role or a CURRENT project that is worded as a goal or a task still to
do ("Establish …", "aiming for …"). Projects joined on the A1-3 gap ruling: the
founder's own U-7 bullets sit on a top-level project.

Three owners, kept apart (ADR-062):

* **Candidate selection is code** — the bullets of a current work entry
  (``is_current`` or no ``end_date``) or a current project (``is_current``; or
  no ``end_date`` with a start date or an association to a current work entry)
  that no verdict has judged yet. A project with an ``end_date`` is never
  current, whatever it is associated with.
* **The judgement is the model's** — ONE ``judge_aspirations`` call at the end of
  a CV import, persisted as ``RecordAspirationVerdicts`` in the import's commit
  (negatives too, so a bullet is judged once). No marker list decides it.
* **The answer is the candidate's** — the hub asks the ``aspiration`` thread
  (class A, resolver ``ask_achieved``). "Ja" + what came out writes that
  outcome as an achievement in the candidate's own words (ADR-061 cl. 2
  testimony through the manual door); "Nein" records the bullet in the entry's
  ``not_yet_reached`` list, which a writer reads as a goal, never a result.

A failed judge call writes no verdicts: nothing is asked (the question is a
courtesy, never a gate) and the next import judges again.
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from applire.schemas.profile import (
    MasterProfileData,
    MasterProfileResponse,
    ProjectEntry,
    WorkEntry,
)

logger = logging.getLogger(__name__)

ANSWER_INTAKE = "aspiration_answer"
ISSUE_PREFIX = "aspiration:"
#: The answer's rationale keys (frontend catalogs ``review.rationale.*``).
RATIONALE_ACHIEVED = "aspiration_achieved"
RATIONALE_GOAL = "aspiration_goal"


class AspirationNotFound(LookupError):
    """No open ``aspiration`` issue with that id (unknown, or the bullet is gone)."""


class AspirationAnswered(Exception):
    """The bullet was already answered."""


class AspirationOutcomeRequired(ValueError):
    """``achieved`` without what came out."""


def aspiration_key(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).casefold().encode("utf-8")).hexdigest()[:12]


def issue_id(entry_id: str, key: str) -> str:
    return f"{ISSUE_PREFIX}{entry_id}:{key}"


SECTIONS = ("work_experience", "projects")
Entry = WorkEntry | ProjectEntry


def is_current(entry: WorkEntry) -> bool:
    return bool(entry.is_current) or (bool(entry.start_date) and not entry.end_date)


def _project_is_current(project: ProjectEntry, current_work: list[WorkEntry]) -> bool:
    if project.is_current:
        return True
    if project.end_date:
        return False
    if project.start_date:
        return True
    link = " ".join((project.associated_experience or "").split()).casefold()
    return bool(link) and any(
        link in {w.id.casefold(), " ".join((w.company or "").split()).casefold()}
        for w in current_work
    )


def current_entries(profile: MasterProfileData) -> list[tuple[str, Entry]]:
    """``(section, entry)`` of every current work entry, then every current project."""
    work = [w for w in profile.work_experience or [] if is_current(w)]
    projects = [p for p in profile.projects or [] if _project_is_current(p, work)]
    return [("work_experience", w) for w in work] + [("projects", p) for p in projects]


def _bullets(entry: Entry) -> list[str]:
    return [b for b in (*entry.responsibilities, *entry.achievements) if isinstance(b, str) and b.strip()]


def entry_label(entry: Entry) -> str:
    if isinstance(entry, ProjectEntry):
        return entry.name
    return f"{entry.role} — {entry.company}".strip(" —")


def unjudged_bullets(profile: MasterProfileData) -> list[tuple[str, str, str, str]]:
    """``(section, entry_id, key, text)`` of every current-role and
    current-project bullet no verdict judged."""
    meta = profile.metadata
    judged = {(v.entry_id, v.key) for v in (meta.aspiration_verdicts if meta else [])}
    out: list[tuple[str, str, str, str]] = []
    for section, entry in current_entries(profile):
        for text in _bullets(entry):
            k = aspiration_key(text)
            if (entry.id, k) not in judged:
                judged.add((entry.id, k))
                out.append((section, entry.id, k, text))
    return out


def open_aspirations(profile: MasterProfileData) -> list[tuple[str, Entry, str, str]]:
    """``(section, entry, key, text)`` for every bullet the model judged as
    goal/task wording that still stands on a current entry and is unanswered."""
    meta = profile.metadata
    if meta is None or not meta.aspiration_verdicts:
        return []
    answered = {(a.entry_id, a.key) for a in meta.aspiration_answers}
    by_id = {e.id: (sec, e) for sec, e in current_entries(profile)}
    out: list[tuple[str, Entry, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for v in meta.aspiration_verdicts:
        ident = (v.entry_id, v.key)
        if not v.aspiration or ident in answered or ident in seen:
            continue
        hit = by_id.get(v.entry_id)
        if hit is None:
            continue
        section, entry = hit
        text = next((b for b in _bullets(entry) if aspiration_key(b) == v.key), None)
        if text is None:
            continue
        seen.add(ident)
        out.append((section, entry, v.key, text))
    return out


async def aspiration_verdict_ops(profile: MasterProfileData, provider) -> list:
    """The import-end judgement (one model call) as a ``RecordAspirationVerdicts``
    op for the import's own commit; ``[]`` on no candidates or any failure."""
    try:
        from applire.services.profile.decisions import judge_aspirations
        from applire.services.profile.reconcile.ops import (
            AspirationVerdictItem,
            RecordAspirationVerdicts,
        )

        candidates = unjudged_bullets(profile)
        if not candidates:
            return []
        flagged = await judge_aspirations(provider, [t for _, _, _, t in candidates])
        if flagged is None:
            return []
        items = [
            AspirationVerdictItem(section=sec, entry_id=eid, key=k, text=t, aspiration=i in flagged)
            for i, (sec, eid, k, t) in enumerate(candidates)
        ]
        return [RecordAspirationVerdicts(verdicts=items)]
    except Exception:  # noqa: BLE001 — a judge failure must never fail the import
        logger.warning("aspiration verdicts skipped", exc_info=True)
        return []


def find_open_aspiration(profile: MasterProfileData, entry_id: str, key: str):
    """``(section, entry, text)`` of an open aspiration, or raise (one predicate for the
    service pre-check and the applier, ADR-066)."""
    meta = profile.metadata
    if meta is not None and any(
        a.entry_id == entry_id and a.key == key for a in meta.aspiration_answers
    ):
        raise AspirationAnswered(key)
    for section, entry, k, text in open_aspirations(profile):
        if entry.id == entry_id and k == key:
            return section, entry, text
    raise AspirationNotFound(key)


def apply_aspiration_op(op, profile: MasterProfileData, changes: list) -> None:
    from applire.schemas.profile import (
        AspirationAnswer,
        AspirationVerdict,
        FieldChange,
        ProfileMetadata,
    )

    if profile.metadata is None:
        profile.metadata = ProfileMetadata()
    meta = profile.metadata
    if op.op == "record_aspiration_verdicts":
        judged = {(v.entry_id, v.key) for v in meta.aspiration_verdicts}
        for item in op.verdicts:
            if (item.entry_id, item.key) in judged:
                continue
            judged.add((item.entry_id, item.key))
            meta.aspiration_verdicts.append(AspirationVerdict(**item.model_dump()))
        return

    section, entry, text = find_open_aspiration(profile, op.entry_id, op.key)
    label = entry_label(entry)
    if op.achieved:
        outcome = " ".join((op.outcome or "").split())
        if not outcome:
            raise AspirationOutcomeRequired(op.key)
        if outcome.casefold() not in {a.casefold() for a in entry.achievements}:
            entry.achievements.append(outcome)
        changes.append(FieldChange(
            section=section, field="achievements", action="added",
            old_value=None, new_value=outcome,
            rationale=f"Added to {label!r}: the candidate said what came out of a goal-worded bullet.",
            rationale_key=RATIONALE_ACHIEVED,
        ))
    else:
        if text not in entry.not_yet_reached:
            entry.not_yet_reached.append(text)
        changes.append(FieldChange(
            section=section, field="not_yet_reached", action="added",
            old_value=None, new_value=text,
            rationale=f"Kept as a goal on {label!r}: the candidate said it is not reached yet.",
            rationale_key=RATIONALE_GOAL,
        ))
    meta.aspiration_answers.append(AspirationAnswer(
        entry_id=op.entry_id, key=op.key, answer="achieved" if op.achieved else "goal",
    ))


def parse_issue_id(value: str) -> tuple[str, str]:
    if not value.startswith(ISSUE_PREFIX):
        raise AspirationNotFound(value)
    entry_id, _, key = value[len(ISSUE_PREFIX):].rpartition(":")
    if not entry_id or not key:
        raise AspirationNotFound(value)
    return entry_id, key


async def answer_aspiration(
    db: AsyncSession,
    *,
    issue_id: str,
    achieved: bool,
    outcome: str | None,
    user_id: uuid.UUID | None = None,
) -> MasterProfileResponse:
    """The hub's "Ist das erreicht?" answer — one ``AnswerAspiration`` op through
    the committer (``manual_edit``, no grounding, no snapshot)."""
    from applire.services.profile import _get_latest, _to_response
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import AnswerAspiration

    entry_id, key = parse_issue_id(issue_id)
    if achieved and not (outcome or "").strip():
        raise AspirationOutcomeRequired(key)
    record = await _get_latest(db, user_id)
    if not record:
        raise LookupError("No profile found")
    find_open_aspiration(MasterProfileData.model_validate(record.profile_json), entry_id, key)
    result = await commit_ops(
        db,
        [AnswerAspiration(entry_id=entry_id, key=key, achieved=achieved, outcome=outcome)],
        CommitProvenance(source="manual_edit", intake=ANSWER_INTAKE, actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
        llm_provider=None,
    )
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)
